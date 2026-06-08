from __future__ import annotations

import json
import re
from typing import Any

from app.models.fair_do import FairDORepository


class GMOVController:
    """Controller layer: mediates the GMOV view and currently loaded FAIR DO models."""

    def __init__(self, fdo_dir=None):
        self.repo = FairDORepository(fdo_dir) if fdo_dir is not None else FairDORepository()

    def model_manifest(self) -> dict[str, Any]:
        models = self.repo.loaded_models()
        ops = self.repo.operations()
        return {
            "model_count": len(models),
            "operation_count": len(ops),
            "executable_operation_count": sum(1 for op in ops if op.executable),
            "models": [m.to_dict() for m in models],
            "operations": [op.to_dict() for op in ops],
            "plain_language_summary": self._summarize(ops),
        }

    # Backward-compatible name for existing routes/tests.
    def capability_manifest(self) -> dict[str, Any]:
        return self.model_manifest()

    def _summarize(self, ops) -> list[str]:
        if not ops:
            return ["No models are loaded. GMOV currently has no model services available."]
        lines = []
        for op in ops:
            status = "ready" if op.executable else "not executable"
            lines.append(f"{op.operation_id}: {op.summary} ({status})")
        return lines

    def handle_natural_language(self, message: str, result_history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Deterministic stand-in for a future LLM-enabled GMOV surface.

        Prototype 5 intentionally avoids raw JSON-style answers. This method
        behaves like a model librarian: it summarizes and searches loaded Models,
        services, status, provenance, and the current result history in concise
        human-readable text while still using deterministic routing under the hood.
        """
        msg = message.strip()
        lower = msg.lower()

        # Search-like questions should search the current Model library instead of
        # falling through to raw service execution or broad inventory dumps.
        search_terms = self._extract_search_terms(lower)
        if search_terms:
            return self._search_response(search_terms, result_history or [], msg)

        if self._is_capability_question(lower):
            return self._capability_response()
        if self._is_inventory_question(msg):
            return self._inventory_response()
        if self._is_status_question(lower):
            return self._status_response()
        if self._is_provenance_question(lower):
            return self._provenance_response(lower)

        operation_id = self._select_operation(msg)
        inputs = self._extract_inputs(msg, operation_id)
        execution = self.repo.execute(operation_id, inputs)
        return self._execution_as_gmov_response(msg, execution)


    def handle_constrained_compute(self, message: str) -> dict[str, Any]:
        """Deterministic stand-in for an LLM operating under MVC constraints.

        This method is intentionally explicit: GMOV may inspect loaded Models and
        Services, but any computational answer must be produced by selecting a
        loaded Service, validating inputs, and asking the Controller to execute it.
        """
        msg = message.strip()
        ops = self.repo.operations()
        ready_ops = [op for op in ops if op.executable]
        rule = "Computations must be performed only through loaded Model Services. GMOV may not invent formulas, substitute outside tools, or compute directly."

        if not ops:
            return {
                "response_type": "Constrained computation",
                "display_text": "No Models are loaded. GMOV cannot compute until FAIR DO Models with executable Services are loaded. No matching Service is available.",
                "controller_trace": [
                    "Request classified as a computation request.",
                    "Controller inspected loaded Models and Services.",
                    "No loaded Services were available.",
                    "Computation refused under GMOV operating rule."
                ],
                "operating_rule": rule,
                "status": "refused",
            }

        considered_sorted = self._rank_services_for_request(msg)

        try:
            selected_op, selection_reason = self._select_service_for_request(msg, considered_sorted)
            operation_id = selected_op.operation_id
        except Exception as e:
            lines = [
                "No loaded Service can be verified for this computation request.",
                "",
                "Operating rule:",
                rule,
                "",
                "What happened:",
                f"• {str(e)}",
                "",
                "Available ready Services:",
            ]
            for op in ready_ops[:10]:
                lines.append(f"• {op.operation_id} — {op.model_title}")
            if len(ready_ops) > 10:
                lines.append(f"• …and {len(ready_ops)-10} more ready Services")
            return {
                "response_type": "Constrained computation",
                "display_text": "\n".join(lines),
                "controller_trace": [
                    "Request classified as a computation request.",
                    f"Controller inspected {len(ops)} declared Service(s).",
                    f"Controller found {len(ready_ops)} ready Service(s).",
                    "No loaded Service matched strongly enough to execute.",
                    "Computation refused under GMOV operating rule."
                ],
                "operating_rule": rule,
                "considered_operations": considered_sorted[:12],
                "status": "refused",
            }

        try:
            inputs = self._extract_inputs(msg, operation_id, selected_op)
        except Exception as e:
            return {
                "response_type": "Constrained computation",
                "display_text": f"GMOV selected a loaded Service, but Controller input validation failed.\n\nSelected Service:\n• {selected_op.operation_id}\n• Model: {selected_op.model_title}\n• Why selected: {selection_reason}\n\nWhat is missing:\n• {str(e)}",
                "controller_trace": [
                    "Request classified as a computation request.",
                    f"Controller inspected {len(ops)} declared Service(s).",
                    f"Selected loaded Service: {selected_op.operation_id}",
                    f"Selection reason: {selection_reason}",
                    "Controller could not validate required inputs.",
                    "Computation refused under GMOV operating rule."
                ],
                "operating_rule": rule,
                "selected_operation": selected_op.to_dict(),
                "considered_operations": considered_sorted[:12],
                "status": "validation_failed",
            }

        validation_lines = []
        if not selected_op.executable:
            validation_lines.append("Selected Service is not Ready.")
        if not isinstance(inputs, dict) or not inputs:
            validation_lines.append("Inputs could not be extracted as a non-empty JSON object.")
        if validation_lines:
            return {
                "response_type": "Constrained computation",
                "display_text": "GMOV selected a Service, but Controller validation failed.\n\n" + "\n".join(f"• {x}" for x in validation_lines),
                "controller_trace": [
                    "Request classified as a computation request.",
                    f"Selected Service: {operation_id}",
                    "Controller validation failed.",
                    "Computation refused under GMOV operating rule."
                ],
                "operating_rule": rule,
                "selected_operation": selected_op.to_dict(),
                "inputs": inputs,
                "status": "validation_failed",
            }

        try:
            execution = self.repo.execute(operation_id, inputs, selected_op.model_dir)
        except Exception as e:
            return {
                "response_type": "Constrained computation",
                "display_text": f"GMOV selected a loaded Service, but Controller execution failed.\n\n• {str(e)}",
                "controller_trace": [
                    "Request classified as a computation request.",
                    f"Selected Service: {operation_id}",
                    f"Validated inputs: {json.dumps(inputs, ensure_ascii=False)}",
                    "Controller attempted execution through the loaded Model Service.",
                    "Execution failed."
                ],
                "operating_rule": rule,
                "selected_operation": selected_op.to_dict(),
                "inputs": inputs,
                "status": "execution_failed",
            }

        result_text = self._humanize_result(execution.get("result"))
        alternatives = [c for c in considered_sorted if c["operation_id"] != operation_id][:5]
        lines = [
            "GMOV used the selected loaded Service to answer the question.",
            "",
            "Operating rule:",
            rule,
            "",
            "Selected Service:",
            f"• {selected_op.operation_id}",
            f"• Model: {selected_op.model_title}",
            f"• Why selected: {selection_reason}",
            "",
            "Validated Inputs:",
            json.dumps(inputs, ensure_ascii=False),
            "",
            "Result:",
            result_text,
            "",
            "Provenance:",
            f"• Model: {selected_op.model_title}",
            f"• Version: {selected_op.model_version or 'Not specified'}",
            f"• Date: {selected_op.model_date or 'Not specified'}",
        ]
        return {
            "response_type": "Controller Trace",
            "display_text": "\n".join(lines),
            "controller_trace": [
                "Request classified as a computation request.",
                f"Controller inspected {len(ops)} declared Service(s).",
                f"Controller found {len(ready_ops)} ready Service(s).",
                f"Selected loaded Service: {selected_op.operation_id}",
                f"Selection reason: {selection_reason}",
                f"Validated inputs: {json.dumps(inputs, ensure_ascii=False)}",
                "Controller executed the selected Service; GMOV did not compute directly.",
                "Returned result with Model provenance."
            ] + [f"Alternative considered: {a['operation_id']} ({'Ready' if a['ready'] else 'Not Ready'})" for a in alternatives],
            "operating_rule": rule,
            "selected_operation": selected_op.to_dict(),
            "considered_operations": considered_sorted[:12],
            "inputs": inputs,
            "execution": execution,
            "status": "completed",
        }

    def _extract_search_terms(self, message: str) -> list[str]:
        stop = {
            "what", "which", "show", "find", "all", "are", "is", "the", "a", "an", "of", "with", "for", "to", "me",
            "models", "model", "operations", "operation", "results", "result", "associated", "involving", "related", "currently",
            "loaded", "available", "anything", "about", "have", "i", "already", "run", "generated", "from", "using"
        }
        # Prefer explicit search contexts, such as "associated with codeine" or "involving poor metabolizers".
        if not any(word in message for word in ["associated", "related", "involving", "search", "find", "show me anything", "already run", "codeine", "metabolizer", "phenotype", "diplotype"]):
            return []
        tokens = re.findall(r"[a-z0-9*_-]+", message.lower())
        terms = [t for t in tokens if len(t) > 2 and t not in stop]
        # Preserve useful clinical words even if short-ish or frequent.
        for special in ["codeine", "poor", "normal", "intermediate", "ultrarapid", "phenotype", "diplotype", "cyp2d6"]:
            if special in message and special not in terms:
                terms.append(special)
        return list(dict.fromkeys(terms))

    def _search_blob(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
        except Exception:
            return str(value).lower()

    def _matches_terms(self, blob: str, terms: list[str]) -> bool:
        return any(term.lower() in blob for term in terms)

    def _search_response(self, terms: list[str], result_history: list[dict[str, Any]], question: str) -> dict[str, Any]:
        models = self.repo.loaded_models()
        ops = self.repo.operations()
        model_matches = []
        operation_matches = []
        result_matches = []

        for model in models:
            blob = self._search_blob(model.to_dict())
            if self._matches_terms(blob, terms):
                model_matches.append(model)

        for op in ops:
            blob = self._search_blob(op.to_dict())
            if self._matches_terms(blob, terms):
                operation_matches.append(op)

        for idx, result in enumerate(result_history or [], start=1):
            blob = self._search_blob(result)
            if self._matches_terms(blob, terms):
                result_matches.append((idx, result))

        total = len(model_matches) + len(operation_matches) + len(result_matches)
        q = ", ".join(f"“{t}”" for t in terms)
        if total == 0:
            text = f"I did not find any loaded Models, Services, or generated Results matching {q}."
        else:
            lines = [f"I found {total} matching item{'s' if total != 1 else ''} for {q}.", ""]
            if model_matches:
                lines.append("Models")
                for m in model_matches[:12]:
                    lines.append(f"• {m.title}")
                    lines.append(f"  Status: {self._status_label(m.validation_status)}")
                    if m.model_kind != "Model":
                        lines.append(f"  Kind: {m.model_kind}")
                if len(model_matches) > 12:
                    lines.append(f"  …and {len(model_matches)-12} more Models")
                lines.append("")
            if operation_matches:
                lines.append("Services")
                for op in operation_matches[:12]:
                    state = "Ready" if op.executable else "Not Ready"
                    lines.append(f"• {op.operation_id}")
                    lines.append(f"  Model: {op.model_title}")
                    lines.append(f"  Status: {state}")
                if len(operation_matches) > 12:
                    lines.append(f"  …and {len(operation_matches)-12} more Services")
                lines.append("")
            if result_matches:
                lines.append("Generated Results")
                for idx, r in result_matches[:8]:
                    title = r.get("title") or f"Result {idx:03d}"
                    subtitle = r.get("subtitle") or "Generated during this GMOV session"
                    lines.append(f"• {title}")
                    lines.append(f"  {subtitle}")
                if len(result_matches) > 8:
                    lines.append(f"  …and {len(result_matches)-8} more Results")
            text = "\n".join(lines).strip()

        return {
            "response_type": "Search",
            "display_text": text,
            "search_terms": terms,
            "match_counts": {
                "models": len(model_matches),
                "operations": len(operation_matches),
                "results": len(result_matches),
            },
        }

    def _is_capability_question(self, message: str) -> bool:
        return any(phrase in message for phrase in [
            "what can gmov do", "what can you do", "what services", "services are available",
            "available services", "current services", "what capabilities", "capabilities"
        ])

    def _capability_response(self) -> dict[str, Any]:
        ops = self.repo.operations()
        ready_ops = [op for op in ops if op.executable]
        if not ready_ops:
            text = "GMOV has no Ready Services right now. Load FAIR DO Models that expose executable Services, then GMOV can compute through those loaded Services."
        else:
            lines = [
                "GMOV can currently compute through these loaded Services:",
                "",
            ]
            for op in ready_ops[:14]:
                summary = op.summary or op.operation_id
                lines.append(f"• {summary}")
                lines.append(f"  Service: {op.operation_id}")
                lines.append(f"  Model: {op.model_title}")
            if len(ready_ops) > 14:
                lines.append(f"• …and {len(ready_ops)-14} more Ready Services")
            lines.extend([
                "",
                "GMOV does not compute outside these loaded Services. If no loaded Service matches a request, the Controller refuses the computation."
            ])
            text = "\n".join(lines)
        return {
            "response_type": "Service discovery",
            "display_text": text,
            "service_count": len(ops),
            "ready_service_count": len(ready_ops),
        }

    def _is_inventory_question(self, message: str) -> bool:
        m = message.lower()
        return any(phrase in m for phrase in [
            "what models", "what is loaded", "what's loaded", "loaded models",
            "what can you do", "available operations", "current model services"
        ])

    def _inventory_response(self) -> dict[str, Any]:
        models = self.repo.loaded_models()
        ops = self.repo.operations()
        ready_ops = [op for op in ops if op.executable]

        if not models:
            text = "No Models are currently loaded. Load one or more FAIR Digital Object ZIP files to give GMOV Models to operate."
        else:
            lines = [
                f"{len(models)} Model{'s' if len(models) != 1 else ''} currently loaded.",
                "",
            ]
            for model in models:
                status = self._status_label(model.validation_status)
                model_ops = [op for op in ops if op.model_dir == model.model_dir]
                op_names = ", ".join(op.operation_id for op in model_ops) if model_ops else "No direct services declared"
                lines.append(f"• {model.title}")
                lines.append(f"  Status: {status}")
                lines.append(f"  Services: {op_names}")
                lines.append("")
            lines.append(f"Ready services: {len(ready_ops)} of {len(ops)} declared.")
            text = "\n".join(lines).strip()

        return {
            "response_type": "Model inventory",
            "display_text": text,
            "model_count": len(models),
            "operation_count": len(ops),
            "executable_operation_count": len(ready_ops),
        }

    def _is_status_question(self, message: str) -> bool:
        return any(word in message for word in ["status", "ready", "partial", "not ready", "not executed", "validation", "problem", "issue"])

    def _is_provenance_question(self, message: str) -> bool:
        return any(word in message for word in ["provenance", "source", "creator", "author", "version", "where did", "citation"])

    def _status_label(self, status: str) -> str:
        if status == "ready":
            return "Ready"
        if status == "partial":
            return "Partial"
        return "Not Executed"

    def _status_response(self) -> dict[str, Any]:
        models = self.repo.loaded_models()
        if not models:
            text = "No Models are loaded, so there is no Model status to report."
        else:
            lines = ["Model status:", ""]
            for model in models:
                lines.append(f"• {model.title}: {self._status_label(model.validation_status)}")
                if model.validation_status != "ready" and model.validation_messages:
                    # Keep this short; details remain available in the window shade.
                    lines.append(f"  Note: {model.validation_messages[0]}")
            text = "\n".join(lines)
        return {"response_type": "Model status", "display_text": text}

    def _provenance_response(self, message: str) -> dict[str, Any]:
        models = self.repo.loaded_models()
        if not models:
            return {"response_type": "Model provenance", "display_text": "No Models are loaded, so there is no provenance to report."}

        # If the question names a model, lead with that model; otherwise summarize all.
        selected = []
        for model in models:
            if any(token and token in message for token in [model.title.lower(), model.model_dir.lower()]):
                selected.append(model)
        if not selected:
            selected = models

        lines = ["Model provenance summary:", ""]
        for model in selected:
            creator = model.creator
            if isinstance(creator, dict):
                creator_text = creator.get("name") or creator.get("@id") or "Not specified"
            elif isinstance(creator, list):
                creator_text = ", ".join(str(c.get("name", c)) if isinstance(c, dict) else str(c) for c in creator)
            else:
                creator_text = str(creator) if creator else "Not specified"
            lines.append(f"• {model.title}")
            lines.append(f"  Version: {model.version or 'Not specified'}")
            lines.append(f"  Date: {model.date or 'Not specified'}")
            lines.append(f"  Creator: {creator_text}")
            lines.append("")
        return {"response_type": "Model provenance", "display_text": "\n".join(lines).strip()}

    def _execution_as_gmov_response(self, question: str, execution: dict[str, Any]) -> dict[str, Any]:
        prov = execution.get("provenance", {})
        model_title = prov.get("model_title", "Loaded Model")
        op_id = execution.get("model_operation", "operation")
        result = execution.get("result")
        lines = [
            "GMOV selected and ran a loaded Model service.",
            "",
            f"Model: {model_title}",
            f"Service: {op_id}",
            f"Inputs: {json.dumps(execution.get('inputs', {}), ensure_ascii=False)}",
            "",
            "Result:",
            self._humanize_result(result),
        ]
        return {
            "response_type": "Model service",
            "display_text": "\n".join(lines),
            "operation_id": op_id,
        }

    def _humanize_result(self, result: Any) -> str:
        if isinstance(result, dict):
            # Prefer compact key/value lines over machine JSON.
            lines = []
            for k, v in result.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"• {k}: {json.dumps(v, ensure_ascii=False)}")
                else:
                    lines.append(f"• {k}: {v}")
            return "\n".join(lines) if lines else "No result values returned."
        if isinstance(result, list):
            return "\n".join(f"• {item}" for item in result) if result else "No result values returned."
        return str(result)

    def _normalize_for_match(self, value: str) -> str:
        value = (value or "").lower()
        value = re.sub(r"[_\-/]+", " ", value)
        value = re.sub(r"[^a-z0-9*]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def _service_match_text(self, op) -> dict[str, str]:
        data = op.to_dict()
        return {
            "service_id": self._normalize_for_match(op.operation_id),
            "model_title": self._normalize_for_match(op.model_title),
            "model_dir": self._normalize_for_match(op.model_dir),
            "summary": self._normalize_for_match(op.summary),
            "description": self._normalize_for_match(op.description),
            "interface_text": self._normalize_for_match(op.interface_text),
            "all": self._normalize_for_match(json.dumps(data, ensure_ascii=False)),
        }

    def _rank_services_for_request(self, message: str) -> list[dict[str, Any]]:
        """Rank loaded Services for a natural-language request.

        This deterministic matcher simulates the routing role a future LLM would
        play, but with strict MVC constraints: it may only choose from currently
        loaded Services. It prioritizes exact Service-name matches, then domain
        terms such as drug/gene names, then task intent such as recommendation,
        phenotype, and diplotype.
        """
        msg_norm = self._normalize_for_match(message)
        tokens = [t for t in re.findall(r"[a-z0-9*]+", msg_norm) if len(t) > 1]
        stop = {"what", "which", "with", "from", "through", "using", "compute", "run", "please", "give", "tell", "need", "want", "service", "model", "gmov", "for", "the", "and", "that", "this", "all", "are", "is", "a", "an", "of", "to"}
        query_terms = [t for t in tokens if t not in stop]
        task_terms = {
            "recommendation": ["recommendation", "recommend", "therapy", "drug", "prescribing"],
            "phenotype": ["phenotype", "metabolizer", "genotype"],
            "diplotype": ["diplotype", "allele", "star"],
        }
        ranked = []
        for op in self.repo.operations():
            fields = self._service_match_text(op)
            score = 0
            reasons: list[str] = []

            # Highest priority: the user names the Service exactly, allowing
            # underscores/hyphens/spaces to vary.
            if fields["service_id"] and fields["service_id"] in msg_norm:
                score += 250
                reasons.append("direct Service-name match")

            # Also support compact exact matching, e.g. Pain_Medication_Service.
            compact_msg = re.sub(r"\s+", "", msg_norm)
            compact_service = re.sub(r"\s+", "", fields["service_id"])
            if compact_service and compact_service in compact_msg:
                score += 250
                if "direct Service-name match" not in reasons:
                    reasons.append("direct Service-name match")

            # Strong phrase/domain matches in model title or model directory.
            for term in query_terms:
                if len(term) < 3:
                    continue
                if term in fields["model_title"]:
                    score += 35
                    reasons.append(f"model title contains '{term}'")
                elif term in fields["model_dir"]:
                    score += 25
                    reasons.append(f"model folder contains '{term}'")
                elif term in fields["service_id"]:
                    score += 20
                    reasons.append(f"Service name contains '{term}'")
                elif term in fields["summary"] or term in fields["description"]:
                    score += 8
                    reasons.append(f"Service metadata contains '{term}'")
                elif term in fields["all"]:
                    score += 2

            # Task intent should favor Services whose metadata reflects the task.
            for task, words in task_terms.items():
                if any(w in msg_norm for w in words):
                    hay = " ".join([fields["service_id"], fields["model_title"], fields["summary"], fields["description"]])
                    if any(w in hay for w in words):
                        score += 30
                        reasons.append(f"task intent: {task}")

            # If the request includes a star-allele diplotype and asks for a phenotype,
            # phenotype Services should dominate over drug recommendation Services.
            if re.search(r"\*\d", message) and ("phenotype" in msg_norm or "metabolizer" in msg_norm):
                if fields["service_id"] == "phenotype service":
                    score += 180
                    reasons.append("diplotype-to-phenotype Service")
                elif "phenotype" in fields["service_id"] or ("genotype" in fields["model_title"] and "phenotype" in fields["model_title"]):
                    score += 120
                    reasons.append("diplotype-to-phenotype request")
                if "recommend" in fields["model_title"] or "rec" in fields["model_dir"]:
                    score -= 35
                    reasons.append("recommendation Service not needed for phenotype lookup")

            # If the request mentions a drug/gene and asks for a recommendation,
            # a CPIC/DPWG recommendation library_service is usually the best fit.
            if "recommend" in msg_norm or "recommendation" in msg_norm:
                if "recommend" in fields["model_title"] or "recommendation" in fields["model_title"]:
                    score += 35
                    reasons.append("recommendation Model")
                if fields["service_id"] == "library service" and ("rec" in fields["model_dir"] or "recommend" in fields["model_title"]):
                    score += 12

            # Penalize generic library_service unless it is attached to a domain-
            # matching Model; otherwise many unrelated library_service objects tie.
            if fields["service_id"] == "library service" and not any(term in fields["model_title"] or term in fields["model_dir"] for term in query_terms if len(term) > 2):
                score -= 20
                reasons.append("generic Service name without domain match")

            if op.executable:
                score += 5
                reasons.append("Ready")
            else:
                score -= 40
                reasons.append("Not Ready")

            ranked.append({
                "operation_id": op.operation_id,
                "service_id": op.operation_id,
                "model_title": op.model_title,
                "model_dir": op.model_dir,
                "ready": op.executable,
                "score": score,
                "reasons": list(dict.fromkeys(reasons))[:8],
            })
        return sorted(ranked, key=lambda x: x["score"], reverse=True)

    def _select_service_for_request(self, message: str, ranked: list[dict[str, Any]] | None = None):
        ranked = ranked or self._rank_services_for_request(message)
        if not ranked:
            raise ValueError("No Services are loaded.")
        positive = [r for r in ranked if r["score"] > 0]
        if not positive or positive[0]["score"] < 25:
            raise ValueError("No loaded Service clearly matches that request. Try naming a Service or using a stronger domain term such as codeine, tramadol, phenotype, or diplotype.")
        top = positive[0]
        second = positive[1] if len(positive) > 1 else None
        # If two different Services are nearly tied and not the same model/service
        # family, ask for clarification rather than choosing the wrong one.
        if second and top["score"] < 180 and (top["score"] - second["score"] <= 5) and (top["model_title"] != second["model_title"]):
            raise ValueError(f"More than one loaded Service may match: {top['operation_id']} on {top['model_title']} and {second['operation_id']} on {second['model_title']}. Please choose one.")
        op = self.repo.find_operation(top["operation_id"], top["model_dir"])
        if not op:
            raise ValueError(f"Matched Service {top['operation_id']!r}, but it is no longer available.")
        reason = "; ".join(top.get("reasons") or ["highest-ranked loaded Service"])
        return op, reason

    # Backward-compatible selector used by older Ask paths.
    def _select_operation(self, message: str) -> str:
        op, _reason = self._select_service_for_request(message)
        return op.operation_id

    def _extract_phenotype(self, message: str) -> str | None:
        m = message.lower()
        phenotypes = [
            ("ultrarapid metabolizer", [r"ultrarapid\s+(?:\w+\s+){0,3}metabolizer", r"ultra\s*rapid\s+(?:\w+\s+){0,3}metabolizer"]),
            ("intermediate metabolizer", [r"intermediate\s+(?:\w+\s+){0,3}metabolizer"]),
            ("normal metabolizer", [r"normal\s+(?:\w+\s+){0,3}metabolizer", r"extensive\s+(?:\w+\s+){0,3}metabolizer"]),
            ("poor metabolizer", [r"poor\s+(?:\w+\s+){0,3}metabolizer"]),
        ]
        for label, patterns in phenotypes:
            if label in m:
                return label
            for pat in patterns:
                if re.search(pat, m):
                    return label
        return None

    def _extract_inputs(self, message: str, operation_id: str, selected_op=None) -> dict[str, Any]:
        # Prefer explicit JSON if present.
        try:
            start, end = message.index("{"), message.rindex("}") + 1
            return json.loads(message[start:end])
        except Exception:
            pass

        op_text = ""
        if selected_op is not None:
            op_text = self._normalize_for_match(" ".join([selected_op.operation_id, selected_op.model_title, selected_op.model_dir, selected_op.summary, selected_op.description]))

        if operation_id == "phenotype_service" or ("phenotype" in op_text and "genotype" in op_text):
            match = re.search(r"\*\d+(?:xN|x\d+)?(?:/\*\d+(?:xN|x\d+)?)?", message)
            if not match:
                raise ValueError("This Service requires a diplotype input such as {'CYP2D6': '*3/*3'}.")
            # Use the gene implied by the Model when possible.
            gene = "CYP2D6"
            for candidate in ["CYP2C19", "CYP2C9", "CYP2D6", "CYP3A5", "SLCO1B1", "TPMT", "UGT1A1", "HLA-A", "HLA-B"]:
                if selected_op and candidate.lower() in selected_op.model_title.lower():
                    gene = candidate
                    break
            return {gene: match.group(0)}

        phenotype = self._extract_phenotype(message)
        if operation_id == "diplotype_service":
            if phenotype:
                return {"CYP2D6": phenotype.title().replace("Metabolizer", "metabolizer")}
            raise ValueError("This Service requires a phenotype such as 'Poor metabolizer'.")

        # Recommendation Services typically accept a phenotype object keyed by gene.
        if operation_id == "library_service" or "recommend" in op_text or "rec" in op_text:
            if phenotype:
                gene = "CYP2D6"
                for candidate in ["CYP2C19", "CYP2C9", "CYP2D6", "CYP3A5", "SLCO1B1", "TPMT", "UGT1A1", "HLA-A", "HLA-B"]:
                    if selected_op and candidate.lower() in selected_op.model_title.lower():
                        gene = candidate
                        break
                return {gene: {"phenotype": phenotype}}
            raise ValueError("This Service requires a phenotype such as 'Poor metabolizer'.")

        raise ValueError(f"No input extractor available for {operation_id}")
