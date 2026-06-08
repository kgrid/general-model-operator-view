from __future__ import annotations

import ast
import difflib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
FDO_DIR = ROOT / "data" / "fdos"
UPLOAD_DIR = ROOT / "uploads"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _first_line(text: str, fallback: str = "") -> str:
    for line in text.splitlines():
        line = line.strip("# ").strip()
        if line:
            return line
    return fallback


def _safe_name(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return name.strip("._") or f"object_{uuid.uuid4().hex[:8]}"


def _unique_dest(parent: Path, preferred: str) -> Path:
    base = _safe_name(preferred)
    dest = parent / base
    if not dest.exists():
        return dest
    return parent / f"{base}_{uuid.uuid4().hex[:6]}"


@dataclass
class Implementation:
    path: str
    type: str = "Implementation"
    exists: bool = False
    callables: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    likely_matches: list[str] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        return self.exists and bool(self.callables) and (self.path.endswith(".py") or self.path.endswith(".js"))


@dataclass
class ModelOperation:
    model_id: str
    model_title: str
    model_version: str
    model_date: str
    model_dir: str
    operation_id: str
    description: str
    interface_file: str | None
    interface_text: str
    implementations: list[Implementation]
    tests: list[str]
    provenance: dict[str, Any]
    validation_status: str = "unknown"
    validation_messages: list[str] = field(default_factory=list)
    operation_type: str = "Direct"
    dependencies: list[str] = field(default_factory=list)
    dependency_details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.model_title} / {self.operation_id}"

    @property
    def executable(self) -> bool:
        return any(i.executable for i in self.implementations)

    @property
    def summary(self) -> str:
        return _first_line(self.interface_text, self.description)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_title": self.model_title,
            "model_version": self.model_version,
            "model_date": self.model_date,
            "model_dir": self.model_dir,
            "operation_id": self.operation_id,
            "service_id": self.operation_id,
            "description": self.description,
            "summary": self.summary,
            "interface_file": self.interface_file,
            "interface_text": self.interface_text,
            "implementations": [i.__dict__ | {"executable": i.executable} for i in self.implementations],
            "tests": self.tests,
            "provenance": self.provenance,
            "validation_status": self.validation_status,
            "validation_messages": self.validation_messages,
            "operation_type": self.operation_type,
            "dependencies": self.dependencies,
            "dependency_details": self.dependency_details,
            "executable": self.executable,
        }


@dataclass
class LoadedModel:
    model_id: str
    title: str
    version: str
    date: str
    model_dir: str
    creator: Any
    operation_count: int
    executable_operation_count: int
    validation_status: str
    validation_messages: list[str]
    model_kind: str = "Model"
    parent_assembly: str = ""
    tags: list[str] = field(default_factory=list)
    has_info_page: bool = False
    info_page_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__


class FairDORepository:
    """Model layer: loads FAIR DO directories and extracts model services from metadata."""

    def __init__(self, fdo_dir: Path = FDO_DIR):
        self.fdo_dir = Path(fdo_dir)
        self.fdo_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir = self.fdo_dir.parent / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._remote_meta_cache: dict[str, dict[str, Any] | None] = {}

    def import_zip(self, zip_file) -> dict[str, Any]:
        """Import one ZIP. If it contains a Knowledge Assembly, resolve local member FDOs."""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        import_id = f"import_{uuid.uuid4().hex[:8]}"
        dest = self.fdo_dir / import_id
        dest.mkdir(parents=True, exist_ok=True)
        zpath = self.upload_dir / f"{import_id}.zip"
        zip_file.save(zpath)
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.infolist():
                if member.filename.startswith("__MACOSX/") or "/.__" in member.filename or Path(member.filename).name.startswith("._"):
                    continue
                zf.extract(member, dest)

        assembly_result = self._import_model_assembly_from_extract(dest)
        if assembly_result:
            shutil.rmtree(dest, ignore_errors=True)
            return assembly_result

        top_dirs = [p for p in dest.iterdir() if p.is_dir()]
        if len(top_dirs) == 1 and (top_dirs[0] / "metadata.json").exists():
            final = _unique_dest(self.fdo_dir, top_dirs[0].name)
            shutil.move(str(top_dirs[0]), final)
            shutil.rmtree(dest)
            model_dir = final.name
        else:
            model_dir = dest.name
        model = self.model_by_dir(model_dir)
        return {"model_dir": model_dir, "model": model.to_dict() if model else None, "loaded_members": []}

    def _import_model_assembly_from_extract(self, extract_root: Path) -> dict[str, Any] | None:
        assembly_candidates: list[tuple[Path, dict[str, Any]]] = []
        for meta_path in extract_root.rglob("metadata.json"):
            meta = self._load_meta(meta_path.parent)
            types = " ".join(str(t) for t in _as_list(meta.get("@type"))) if meta else ""
            if meta and "KnowledgeAssembly" in types:
                assembly_candidates.append((meta_path.parent, meta))
        if not assembly_candidates:
            return None

        assembly_src, assembly_meta = assembly_candidates[0]
        assembly_dest = _unique_dest(self.fdo_dir, assembly_src.name)
        shutil.copytree(assembly_src, assembly_dest)

        # Prototype 10: enrich linked KnowledgeSet references, such as KS2,
        # inside the copied Assembly metadata. This preserves the provenance
        # chain while making later dependency checks independent of repeated
        # remote lookups.
        assembly_meta = self._enrich_linked_knowledge_sets(assembly_meta, extract_root)
        try:
            (assembly_dest / "metadata.json").write_text(json.dumps(assembly_meta, indent=2), encoding="utf-8")
        except Exception:
            pass

        referenced_names = self._assembly_referenced_folder_names(assembly_meta, extract_root)
        loaded_members: list[dict[str, str]] = []
        seen_sources: set[str] = set()
        for folder_name in referenced_names:
            for candidate in extract_root.rglob(folder_name):
                if not candidate.is_dir() or not (candidate / "metadata.json").exists():
                    continue
                if str(candidate.resolve()) in seen_sources:
                    continue
                seen_sources.add(str(candidate.resolve()))
                member_dest = _unique_dest(self.fdo_dir, candidate.name)
                shutil.copytree(candidate, member_dest)
                try:
                    (member_dest / ".gmov_parent_assembly").write_text(assembly_dest.name, encoding="utf-8")
                except Exception:
                    pass
                loaded_members.append({"title": candidate.name, "model_dir": member_dest.name, "source": str(candidate.relative_to(extract_root))})
                break

        model = self.model_by_dir(assembly_dest.name)
        return {
            "model_dir": assembly_dest.name,
            "model": model.to_dict() if model else None,
            "loaded_members": loaded_members,
            "assembly_resolution": {
                "referenced_count": len(referenced_names),
                "loaded_member_count": len(loaded_members),
            },
        }

    def _extract_knowledge_object_folder_names_from_meta(self, meta: dict[str, Any]) -> list[str]:
        """Extract likely member FAIR DO folder names from KnowledgeSet metadata."""
        names: list[str] = []

        def add_ref(ref: str) -> None:
            cleaned = ref.rstrip('/').split('/')[-1]
            if cleaned and cleaned not in {"main", "collection", "metadata.json", "pgx-ka", "ks1", "ks2", "ks3"}:
                names.append(cleaned)

        for key in ["koio:hasKnowledgeObject", "hasKnowledgeObject", "https://kgrid.org/koio#hasKnowledgeObject"]:
            for ko in _as_list(meta.get(key)):
                if isinstance(ko, dict) and isinstance(ko.get("@id"), str):
                    add_ref(ko["@id"])
                elif isinstance(ko, str):
                    add_ref(ko)
        return list(dict.fromkeys(names))

    def _fetch_remote_metadata(self, url: str) -> dict[str, Any] | None:
        """Fetch remote JSON metadata for linked Knowledge Sets.

        Prototype 10 uses this only for explicit metadata.json URLs in a
        Model Assembly metadata chain. It does not search the web or invent
        locations; it follows the link already present in the FAIR DO metadata.
        """
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return None
        if url in self._remote_meta_cache:
            return self._remote_meta_cache[url]
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GMOV-prototype/10"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status >= 400:
                    self._remote_meta_cache[url] = None
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                self._remote_meta_cache[url] = data
                return data
        except Exception:
            self._remote_meta_cache[url] = None
            return None

    def _resolve_knowledge_set_metadata(self, ref: str, search_root: Path) -> dict[str, Any] | None:
        """Resolve a KnowledgeSet metadata.json reference locally, then remotely.

        Local resolution keeps demos reproducible when the Assembly ZIP bundles
        the referenced set. Remote resolution supports Assembly metadata that
        points to an openly available KnowledgeSet document such as KS2.
        """
        if not isinstance(ref, str) or not ref.rstrip('/').endswith('metadata.json'):
            return None
        cleaned = ref.rstrip('/').split('/')[-1]
        parent_hint = ref.rstrip('/').split('/')[-2] if len(ref.rstrip('/').split('/')) >= 2 else ''
        roots = [search_root, search_root.parent]
        for root in roots:
            candidates: list[Path] = []
            if parent_hint:
                candidates.extend(p / cleaned for p in root.rglob(parent_hint) if p.is_dir() and (p / cleaned).exists())
            candidates.extend(root.rglob(cleaned))
            for meta_path in candidates:
                meta = self._load_meta(meta_path.parent)
                if meta and self._extract_knowledge_object_folder_names_from_meta(meta):
                    return meta
        return self._fetch_remote_metadata(ref)

    def _enrich_linked_knowledge_sets(self, assembly_meta: dict[str, Any], extract_root: Path) -> dict[str, Any]:
        """Inline KnowledgeObject references from linked KnowledgeSet metadata.

        Example: KS2 in the PGx Model Assembly points to a remote
        drug_recommendation_knowledge_set/metadata.json. The uploaded Assembly
        also contains that metadata locally. GMOV follows the metadata link,
        reads the KnowledgeObject references, and keeps them inside the loaded
        Assembly copy so dependency readiness can be computed consistently.
        """
        enriched = json.loads(json.dumps(assembly_meta))
        hk_key = "hasKnowledge" if "hasKnowledge" in enriched else "koio:hasKnowledge"
        items = _as_list(enriched.get(hk_key))
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("@id")
            if isinstance(ref, str) and ref.rstrip('/').endswith('metadata.json'):
                resolved = self._resolve_knowledge_set_metadata(ref, extract_root)
                if resolved:
                    for key in ["koio:hasKnowledgeObject", "hasKnowledgeObject", "https://kgrid.org/koio#hasKnowledgeObject"]:
                        if key in resolved and key not in item:
                            item[key] = resolved[key]
                            changed = True
                    if not item.get("dc:description") and resolved.get("dc:description"):
                        item["dc:description"] = resolved["dc:description"]
                        changed = True
        if changed:
            enriched[hk_key] = items
        return enriched

    def _assembly_referenced_folder_names(self, assembly_meta: dict[str, Any], extract_root: Path) -> list[str]:
        """Return member FAIR DO folder names referenced by a Knowledge Assembly.

        Prototype 10 follows the Assembly metadata chain. Direct KnowledgeSet
        blocks are read in-place. Linked KnowledgeSet metadata documents such
        as KS2 are resolved first from the uploaded ZIP and, if absent, from the
        explicit remote metadata.json URL in the Assembly metadata.
        """
        names: list[str] = []

        def add_ref(ref: str) -> None:
            cleaned = ref.rstrip('/').split('/')[-1]
            if cleaned and cleaned not in {"main", "collection", "metadata.json", "pgx-ka", "ks1", "ks2", "ks3"}:
                names.append(cleaned)

        for item in _as_list(assembly_meta.get("hasKnowledge") or assembly_meta.get("koio:hasKnowledge") or assembly_meta.get("https://kgrid.org/koio#hasKnowledge")):
            if not isinstance(item, dict):
                continue
            for fname in self._extract_knowledge_object_folder_names_from_meta(item):
                add_ref(fname)
            ref = item.get("@id")
            if isinstance(ref, str):
                if ref.rstrip('/').endswith('metadata.json'):
                    meta = self._resolve_knowledge_set_metadata(ref, extract_root)
                    if meta:
                        for fname in self._extract_knowledge_object_folder_names_from_meta(meta):
                            add_ref(fname)
                else:
                    cleaned = ref.rstrip('/').split('/')[-1]
                    if cleaned not in {"ks1", "ks2", "ks3"}:
                        add_ref(ref)

        out: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name and name not in seen:
                seen.add(name)
                out.append(name)
        return out


    def unload_model(self, model_dir: str) -> dict[str, Any]:
        """Unload one loaded Model from GMOV.

        Unloading removes the extracted FDO folder from GMOV's local loaded-model
        library. If the selected Model is a Model Assembly, its member Models are
        unloaded with it. Assembly-owned member Models cannot be unloaded
        independently, because doing so leaves the Assembly dependency graph in a
        confusing partial state.
        """
        safe_name = Path(str(model_dir)).name
        obj_dir = self.fdo_dir / safe_name
        if not obj_dir.exists() or not obj_dir.is_dir():
            raise ValueError(f"Loaded Model not found: {safe_name}")

        meta = self._load_meta(obj_dir) or {}
        title = meta.get("dc:title", safe_name)
        types = " ".join(str(t) for t in _as_list(meta.get("@type")))
        is_assembly = "KnowledgeAssembly" in types

        parent_assembly = ""
        try:
            parent_assembly = (obj_dir / ".gmov_parent_assembly").read_text(encoding="utf-8").strip()
        except Exception:
            parent_assembly = ""

        if parent_assembly and not is_assembly:
            raise ValueError(f"'{title}' is a member of Model Assembly '{parent_assembly}'. Unload the Assembly to remove its members.")

        removed: list[dict[str, str]] = []

        def remove_dir(d: Path) -> None:
            m = self._load_meta(d) or {}
            removed.append({"model_dir": d.name, "title": m.get("dc:title", d.name)})
            shutil.rmtree(d, ignore_errors=True)

        if is_assembly:
            for child in list(self.list_object_dirs()):
                try:
                    if (child / ".gmov_parent_assembly").read_text(encoding="utf-8").strip() == safe_name:
                        remove_dir(child)
                except Exception:
                    pass
        remove_dir(obj_dir)
        return {"unloaded": removed, "unloaded_count": len(removed), "model_dir": safe_name, "title": title}

    def unload_all(self) -> dict[str, Any]:
        """Unload every currently loaded Model from GMOV."""
        removed: list[dict[str, str]] = []
        for obj_dir in list(self.list_object_dirs()):
            meta = self._load_meta(obj_dir) or {}
            removed.append({"model_dir": obj_dir.name, "title": meta.get("dc:title", obj_dir.name)})
            # Some metadata.json files can be nested below the same parent; ignore
            # errors after the first removal.
            shutil.rmtree(obj_dir, ignore_errors=True)
        # Clear remote metadata cache because future Assembly loads should start
        # from the newly loaded state.
        self._remote_meta_cache = {}
        return {"unloaded": removed, "unloaded_count": len(removed)}

    def list_object_dirs(self) -> list[Path]:
        return sorted({p.parent for p in self.fdo_dir.rglob("metadata.json")})

    def _load_meta(self, obj_dir: Path) -> dict[str, Any] | None:
        try:
            return json.loads((obj_dir / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def loaded_models(self) -> list[LoadedModel]:
        models: list[LoadedModel] = []
        ops_by_dir: dict[str, list[ModelOperation]] = {}
        for op in self.operations():
            ops_by_dir.setdefault(op.model_dir, []).append(op)

        for obj_dir in self.list_object_dirs():
            meta = self._load_meta(obj_dir)
            if not meta:
                continue
            ops = ops_by_dir.get(obj_dir.name, [])
            messages: list[str] = []
            types = " ".join(str(t) for t in _as_list(meta.get("@type")))
            model_kind = "Model Assembly" if "KnowledgeAssembly" in types else ("Knowledge Set" if "KnowledgeSet" in types else "Model")
            if not ops and model_kind == "Model":
                messages.append("No directly executable model services are declared in metadata.")
            for op in ops:
                messages.extend(op.validation_messages)
            if model_kind == "Model Assembly":
                status = "ready" if any(op.executable for op in ops) else "partial"
            else:
                status = "ready" if ops and all(op.executable for op in ops) else ("partial" if any(op.executable for op in ops) else "not_executable")
            parent_assembly = ""
            try:
                parent_assembly = (obj_dir / ".gmov_parent_assembly").read_text(encoding="utf-8").strip()
            except Exception:
                parent_assembly = ""
            tags = self._model_tags(meta, ops, model_kind, status)
            has_info_page = (obj_dir / "index.html").exists() and (obj_dir / "index.html").is_file()
            models.append(LoadedModel(
                model_id=meta.get("dc:identifier") or meta.get("@id") or obj_dir.name,
                title=meta.get("dc:title", obj_dir.name),
                version=meta.get("dc:version", meta.get("versionInfo", "")),
                date=meta.get("dc:date", ""),
                model_dir=obj_dir.name,
                creator=meta.get("schema:creator", {}),
                operation_count=len(ops),
                executable_operation_count=sum(1 for op in ops if op.executable),
                validation_status=status,
                validation_messages=messages,
                model_kind=model_kind,
                parent_assembly=parent_assembly,
                tags=tags,
                has_info_page=has_info_page,
                info_page_url=f"/fdo-info/{obj_dir.name}/" if has_info_page else "",
            ))
        return models

    def _model_tags(self, meta: dict[str, Any], ops: list[ModelOperation], model_kind: str, status: str) -> list[str]:
        text = " ".join([
            str(meta.get("dc:title", "")),
            str(meta.get("dc:description", "")),
            str(meta.get("dc:identifier", "")),
            str(meta.get("@id", "")),
            model_kind,
            status,
            " ".join(op.operation_id for op in ops),
            " ".join(op.description for op in ops),
        ]).lower()
        preferred = [
            "codeine", "tramadol", "opioid", "pain", "cyp2d6", "cyp2c19", "cyp2c9", "cyp3a5",
            "slco1b1", "tpmt", "ugt1a1", "cpic", "dpwg", "pharmacogenomics", "phenotype",
            "diplotype", "recommendation", "assembly", "ready", "partial"
        ]
        tags = [t for t in preferred if t in text]
        if model_kind == "Model Assembly" and "assembly" not in tags:
            tags.append("assembly")
        if status and status not in tags:
            tags.append(status)
        return list(dict.fromkeys(tags))[:10]

    def _knowledge_set_map(self, meta: dict[str, Any], extract_root: Path | None = None) -> dict[str, dict[str, Any]]:
        """Map Knowledge Set identifiers to metadata blocks.

        Linked KnowledgeSet documents are resolved and merged into the local
        block. This lets KS2-style remote Knowledge Sets contribute their
        koio:hasKnowledgeObject references to Assembly readiness checks.
        """
        out: dict[str, dict[str, Any]] = {}
        for item in _as_list(meta.get("hasKnowledge") or meta.get("koio:hasKnowledge") or meta.get("https://kgrid.org/koio#hasKnowledge")):
            if not isinstance(item, dict):
                continue
            enriched = dict(item)
            ref = enriched.get("@id")
            if isinstance(ref, str) and ref.rstrip('/').endswith('metadata.json'):
                resolved = self._resolve_knowledge_set_metadata(ref, extract_root or self.fdo_dir)
                if resolved:
                    # Keep the local title/id (e.g., ks2) but add remote KnowledgeObject references.
                    for key in ["koio:hasKnowledgeObject", "hasKnowledgeObject", "https://kgrid.org/koio#hasKnowledgeObject"]:
                        if key in resolved and key not in enriched:
                            enriched[key] = resolved[key]
                    if not enriched.get("dc:description") and resolved.get("dc:description"):
                        enriched["dc:description"] = resolved.get("dc:description")
            keys = []
            for k in [enriched.get("@id"), enriched.get("dc:title"), enriched.get("title")]:
                if isinstance(k, str) and k:
                    keys.append(k)
                    keys.append(k.rstrip('/').split('/')[-1])
            for k in keys:
                out[k] = enriched
        return out

    def _knowledge_object_folder_names(self, knowledge_item: dict[str, Any], obj_dir: Path) -> list[str]:
        """Return likely FDO folder names referenced by one Knowledge Set block."""
        if not isinstance(knowledge_item, dict):
            return []
        names = self._extract_knowledge_object_folder_names_from_meta(knowledge_item)
        ref = knowledge_item.get("@id")
        if isinstance(ref, str) and ref.rstrip('/').endswith('metadata.json'):
            meta = self._resolve_knowledge_set_metadata(ref, obj_dir)
            if meta:
                names.extend(self._extract_knowledge_object_folder_names_from_meta(meta))
        return list(dict.fromkeys(names))

    def _assembly_dependency_details(self, obj_dir: Path, meta: dict[str, Any], dependencies: list[str]) -> list[dict[str, Any]]:
        """Describe dependency readiness for Assembly services.

        This is deliberately generic Level 2 behavior. It answers: which
        Knowledge Sets are named by the service, what member Model folders do
        those Knowledge Sets reference, and which of those member Models have
        been loaded into GMOV? It does not yet execute a workflow from metadata.
        """
        if not dependencies:
            return []
        kmap = self._knowledge_set_map(meta, obj_dir)
        loaded = []
        for pth in self.list_object_dirs():
            m = self._load_meta(pth) or {}
            loaded.append({
                "model_dir": pth.name,
                "title": m.get("dc:title", pth.name),
                "validation_status": "loaded",
            })
        details: list[dict[str, Any]] = []
        for dep in dependencies:
            item = kmap.get(dep) or kmap.get(str(dep).rstrip('/').split('/')[-1])
            folder_names = self._knowledge_object_folder_names(item, obj_dir) if item else []
            loaded_matches = []
            missing = []
            for fname in folder_names:
                match = next((m for m in loaded if m["model_dir"].startswith(fname) or fname.lower() in (m["model_dir"] + ' ' + m["title"]).lower()), None)
                if match:
                    loaded_matches.append({"title": match["title"], "model_dir": match["model_dir"], "status": match["validation_status"]})
                else:
                    missing.append(fname)
            if folder_names:
                status = "ready" if not missing else ("partial" if loaded_matches else "not_executable")
            else:
                status = "partial" if item else "not_executable"
            details.append({
                "id": dep,
                "title": item.get("dc:title", dep) if isinstance(item, dict) else dep,
                "description": item.get("dc:description", "") if isinstance(item, dict) else "Dependency metadata not found.",
                "expected_model_count": len(folder_names),
                "loaded_model_count": len(loaded_matches),
                "loaded_models": loaded_matches[:50],
                "missing_models": missing[:50],
                "status": status,
            })
        return details

    def model_by_dir(self, model_dir: str) -> LoadedModel | None:
        return next((m for m in self.loaded_models() if m.model_dir == model_dir), None)

    def operations(self) -> list[ModelOperation]:
        ops: list[ModelOperation] = []
        for obj_dir in self.list_object_dirs():
            meta = self._load_meta(obj_dir)
            if not meta:
                continue
            model_id = meta.get("dc:identifier") or meta.get("@id") or obj_dir.name
            title = meta.get("dc:title", obj_dir.name)
            version = meta.get("dc:version", meta.get("versionInfo", ""))
            date = meta.get("dc:date", "")
            creator = meta.get("schema:creator", {})
            knowledge = _as_list(meta.get("koio:hasKnowledge") or meta.get("hasKnowledge"))
            types = " ".join(str(t) for t in _as_list(meta.get("@type")))
            is_assembly = "KnowledgeAssembly" in types
            services = _as_list(meta.get("hasService"))
            for svc in services:
                interface = svc.get("hasInterface") or svc.get("koio:hasInterface")
                interface_text = _read_text(obj_dir / interface) if interface else ""
                impls: list[Implementation] = []
                messages: list[str] = []
                dependencies = [str(d) for d in _as_list(svc.get("dependsOn"))]
                operation_type = "Orchestration" if is_assembly else "Direct"
                dependency_details = self._assembly_dependency_details(obj_dir, meta, dependencies) if is_assembly else []
                for imp in _as_list(svc.get("implementedBy")):
                    rel = imp.get("@id") if isinstance(imp, dict) else str(imp)
                    imp_type = ", ".join(_as_list(imp.get("@type"))) if isinstance(imp, dict) else "Implementation"
                    impl_path = obj_dir / rel
                    exists = impl_path.exists()
                    runnable_path = rel
                    if exists and impl_path.is_dir():
                        # KOIO implementations often name a package/folder rather than a file.
                        if (impl_path / "__init__.py").exists():
                            runnable_path = rel.rstrip("/") + "/__init__.py"
                            impl_path = obj_dir / runnable_path
                        elif (impl_path / "library-service.js").exists():
                            runnable_path = rel.rstrip("/") + "/library-service.js"
                            impl_path = obj_dir / runnable_path
                        elif (impl_path / "plugin-service.js").exists():
                            runnable_path = rel.rstrip("/") + "/plugin-service.js"
                            impl_path = obj_dir / runnable_path
                    exists = impl_path.exists()
                    if runnable_path.endswith(".py") and exists:
                        callables = self._callables_in_python(impl_path)
                    elif runnable_path.endswith(".js") and exists:
                        callables = self._callables_in_javascript(impl_path)
                    else:
                        callables = []
                    problems: list[str] = []
                    likely: list[str] = []
                    if runnable_path.endswith(".py") or runnable_path.endswith(".js"):
                        lang = "Python" if runnable_path.endswith(".py") else "JavaScript"
                        glob = "*.py" if runnable_path.endswith(".py") else "*.js"
                        if not exists:
                            problems.append(f"Referenced {lang} implementation not found: {rel}")
                            candidates = [str(p.relative_to(obj_dir)) for p in obj_dir.rglob(glob)]
                            likely = difflib.get_close_matches(rel, candidates, n=3, cutoff=0.45)
                            if likely:
                                problems.append("Likely match: " + ", ".join(likely))
                        elif not callables:
                            problems.append(f"{lang} implementation has no detectable exported/public function definitions: {runnable_path}")
                    else:
                        if not exists:
                            problems.append(f"Referenced implementation not found or not currently runnable by GMOV: {rel}")
                        elif impl_path.is_dir():
                            problems.append(f"Implementation folder has no recognized runnable entry point: {rel}")
                    impls.append(Implementation(path=runnable_path, type=imp_type, exists=exists, callables=callables, problems=problems, likely_matches=likely))
                    messages.extend(problems)
                tests = []
                for t in _as_list(svc.get("hasTest")):
                    ib = t.get("implementedBy", {}) if isinstance(t, dict) else {}
                    if isinstance(ib, dict) and ib.get("@id"):
                        tests.append(ib["@id"])
                if dependency_details:
                    not_ready = [d for d in dependency_details if d.get("status") == "not_executable"]
                    partial = [d for d in dependency_details if d.get("status") == "partial"]
                    if not_ready:
                        messages.append("One or more orchestration dependencies could not be resolved from the loaded Model Assembly metadata.")
                    elif partial:
                        messages.append("One or more orchestration dependencies are only partially resolved from loaded Models.")
                status = "ready" if any(i.executable for i in impls) and not any(d.get("status") == "not_executable" for d in dependency_details) else ("partial" if any(i.executable for i in impls) or dependency_details else "not_executable")
                if not impls:
                    status = "not_executable"
                    messages.append("No implementation is declared for this service.")
                if operation_type == "Orchestration" and not any(i.executable for i in impls):
                    messages.append("GMOV can resolve this service's declared dependencies, but cannot execute it without a runnable implementation or explicit execution-graph metadata.")
                ops.append(ModelOperation(
                    model_id=model_id,
                    model_title=title,
                    model_version=version,
                    model_date=date,
                    model_dir=obj_dir.name,
                    operation_id=svc.get("@id", "operation"),
                    description=svc.get("dc:description", ""),
                    interface_file=interface,
                    interface_text=interface_text,
                    implementations=impls,
                    tests=tests,
                    provenance={"creator": creator, "knowledge": knowledge},
                    validation_status=status,
                    validation_messages=messages,
                    operation_type=operation_type,
                    dependencies=dependencies,
                    dependency_details=dependency_details,
                ))
        return ops

    def _callables_in_python(self, path: Path) -> list[str]:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
            # Package entry points often re-export run from a submodule in __init__.py.
            for n in tree.body:
                if isinstance(n, ast.ImportFrom):
                    for alias in n.names:
                        nm = alias.asname or alias.name
                        if nm and not nm.startswith("_"):
                            names.append(nm)
            return list(dict.fromkeys(names))
        except Exception:
            return []

    def _callables_in_javascript(self, path: Path) -> list[str]:
        text = _read_text(path)
        names: list[str] = []
        names.extend(re.findall(r"export\s+function\s+([A-Za-z_$][\w$]*)", text))
        for block in re.findall(r"export\s*\{([^}]+)\}", text):
            for part in block.split(','):
                name = part.strip().split(' as ')[0].strip()
                if re.match(r"^[A-Za-z_$][\w$]*$", name):
                    names.append(name)
        if "export default" in text and re.search(r"\brun\s*[:}]", text):
            names.append("run")
        if not names:
            names.extend(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", text))
        return list(dict.fromkeys(n for n in names if not n.startswith('_')))

    def find_operation(self, operation_id: str, model_dir: str | None = None) -> ModelOperation | None:
        return next((c for c in self.operations() if c.operation_id == operation_id and (model_dir is None or c.model_dir == model_dir)), None)

    def execute(self, operation_id: str, inputs: dict[str, Any], model_dir: str | None = None) -> dict[str, Any]:
        op = self.find_operation(operation_id, model_dir)
        if not op:
            raise ValueError(f"No loaded model service named {operation_id!r}")
        # Prototype 8 Level 2 orchestration policy:
        # - Orchestration operations are first-class services.
        # - GMOV resolves and displays dependencies generically from metadata.
        # - If the Assembly supplies an executable implementation, GMOV runs that
        #   implementation through the normal Python/JavaScript runtime adapters.
        # - GMOV does not hard-code operation-specific workflows and does not
        #   invent execution graphs when metadata lacks step/order/mapping details.
        impl = next((i for i in op.implementations if i.executable and i.path.endswith(".py")), None)
        if not impl:
            impl = next((i for i in op.implementations if i.executable and i.path.endswith(".js")), None)
        if not impl:
            problems = "; ".join(op.validation_messages) or "No executable Python or JavaScript implementation is available."
            raise ValueError(f"Model service {operation_id!r} is not executable: {problems}")
        obj_dir = next(p for p in self.list_object_dirs() if p.name == op.model_dir)
        module_path = obj_dir / impl.path
        fn_name = impl.callables[0]
        if impl.path.endswith(".py"):
            result = self._execute_python(obj_dir, module_path, fn_name, inputs)
            runtime = "Python"
        else:
            result = self._execute_javascript(obj_dir, module_path, fn_name, inputs)
            runtime = "JavaScript"
        return {
            "model_operation": operation_id,
            "runtime": runtime,
            "function": fn_name,
            "inputs": inputs,
            "result": result,
            "execution_trace": [
                f"Selected loaded model: {op.model_title}",
                f"Selected model service: {operation_id}",
                f"Validated executable implementation: {impl.path}",
                f"Executed {runtime} function: {fn_name}",
            ],
            "provenance": op.to_dict(),
        }

    def _execute_python(self, obj_dir: Path, module_path: Path, fn_name: str, inputs: dict[str, Any]) -> Any:
        module_name = f"gmov_{uuid.uuid4().hex}"
        added_paths = [str(obj_dir)] + [str(p) for p in self.list_object_dirs()]
        for pth in reversed(added_paths):
            if pth not in sys.path:
                sys.path.insert(0, pth)
        try:
            if module_path.name == "__init__.py":
                module_name = module_path.parent.name
                spec = importlib.util.spec_from_file_location(module_name, module_path, submodule_search_locations=[str(module_path.parent)])
            else:
                spec = importlib.util.spec_from_file_location(module_name, module_path)
            module = importlib.util.module_from_spec(spec)
            assert spec and spec.loader
            # Make package-style relative imports work for bundled orchestration services.
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            fn: Callable = getattr(module, fn_name)
            return fn(inputs)
        finally:
            for pth in added_paths:
                try:
                    sys.path.remove(pth)
                except ValueError:
                    pass

    def _execute_javascript(self, obj_dir: Path, module_path: Path, fn_name: str, inputs: dict[str, Any]) -> Any:
        if not shutil.which("node"):
            raise RuntimeError("JavaScript execution requires Node.js to be installed and available on PATH.")
        wrapper = obj_dir / f".gmov_runner_{uuid.uuid4().hex}.mjs"
        rel_module = "./" + str(module_path.relative_to(obj_dir)).replace(os.sep, "/")
        node_modules = obj_dir / "node_modules"
        created_links: list[Path] = []
        # Generic Level 2 runtime adapter: expose loaded JavaScript FAIR DO
        # packages to Assembly implementations that use bare package imports.
        # This is not specific to a particular orchestration service.
        try:
            node_modules.mkdir(exist_ok=True)
            for pkg_path in self.fdo_dir.rglob("package.json"):
                try:
                    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                pkg_name = pkg.get("name")
                if not isinstance(pkg_name, str) or not pkg_name:
                    continue
                target = node_modules / pkg_name
                if target.exists():
                    continue
                try:
                    target.symlink_to(pkg_path.parent.resolve(), target_is_directory=True)
                    created_links.append(target)
                except Exception:
                    # Fall back silently; Node will report a missing module if this package is required.
                    pass

            wrapper.write_text(f"""
import * as mod from {json.dumps(rel_module)};
const service = mod.default || mod;
const input = JSON.parse(process.argv[2]);
if (typeof service.initialize === 'function') {{ await service.initialize(); }}
const fn = service[{json.dumps(fn_name)}] || mod[{json.dumps(fn_name)}] || service.run || mod.run;
if (typeof fn !== 'function') {{ throw new Error('No runnable JavaScript function found for {fn_name}.'); }}
const output = await fn(input);
console.log(JSON.stringify(output));
""", encoding="utf-8")
            proc = subprocess.run(["node", str(wrapper), json.dumps(inputs)], cwd=str(obj_dir), text=True, capture_output=True, timeout=30)
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "JavaScript execution failed.")
            try:
                return json.loads(proc.stdout)
            except Exception:
                return proc.stdout.strip()
        finally:
            try:
                wrapper.unlink()
            except Exception:
                pass
            for link in created_links:
                try:
                    if link.is_symlink():
                        link.unlink()
                except Exception:
                    pass
