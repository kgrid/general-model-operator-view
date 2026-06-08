# General Model Operator View (GMOV)

## Provider of the Experimental Application
The Knowledge Systems Lab (KSL) at the University of Michigan created GMOV.  It is stricly an experimental application meant to show how metadata that make digital objects FAIR can be used in a "Model Player." 

## Overview

The General Model Operator View (GMOV) is a prototype environment for loading, inspecting, and executing computational capabilities exposed through FAIR Digital Objects (FDOs). GMOV demonstrates how computational models packaged as FDOs can be made available as agent-usable Services within a Model–View–Controller (MVC) architecture.

The project explores a simple but powerful idea: rather than allowing AI agents to perform arbitrary computations directly, agents can be constrained to invoke trusted computational Services exposed by loaded FDOs. This approach supports explicit model selection, computational provenance, and transparent execution.

GMOV is intended as a research and demonstration platform for FAIR Digital Objects, Computable Biomedical Knowledge (CBK), agent-tool interaction, and provenance-preserving AI-enabled computation.

---

## Core Concepts

### FAIR Digital Objects (FDOs)

GMOV loads FDOs that contain one or more computational Models together with metadata describing:

- Identity
- Provenance
- Version
- Inputs
- Outputs
- Services
- Validation assets
- Documentation

### Models

In GMOV, a Model is a computational artifact loaded from an FDO.

Examples include:

- Phenotype determination models
- Clinical recommendation models
- Risk calculators
- Computational workflows
- Orchestration models

### Services

Models expose executable Services.

Services represent the callable computational capabilities made available to users and agents.

Examples:

- CYP2D6 phenotype lookup
- Codeine recommendation generation
- Tramadol recommendation generation

### Agent

GMOV includes an Agent interface capable of receiving natural-language requests.

The Agent does not perform computations directly. Instead, it:

1. Interprets user requests
2. Identifies candidate Services
3. Selects an appropriate Service
4. Invokes the Controller
5. Returns results and provenance

### Controller

The Controller mediates between user requests and available Services.

Responsibilities include:

- Service discovery
- Service selection
- Input validation
- Service execution
- Provenance capture

---

## MVC Architecture

GMOV follows the Model–View–Controller pattern.

### Model

Loaded FDOs and their executable Services.

### View

The GMOV user interface.

### Controller

The execution and routing layer that mediates between user requests and available Services.

This architecture makes it possible to constrain agent behavior to loaded computational capabilities while preserving transparency and reproducibility.

---

## Key Features

### Load FDOs

Load individual FAIR Digital Objects into the environment.

### Load Model Assemblies

Model Assemblies may reference additional FDOs and Knowledge Sets.

GMOV resolves these dependencies and loads referenced Models when available.

### Service Execution

Execute Services directly through the user interface.

### Agent-Based Computation

Submit natural-language requests to the Agent.

The Agent selects and invokes available Services rather than generating computational results independently.

### Controller Trace

GMOV displays:

- Services considered
- Services rejected
- Selected Service
- Validation status
- Execution status

### Computational Provenance

Results include provenance describing:

- Model used
- Service used
- Execution context

### Information Pages

If an FDO contains a top-level `index.html`, GMOV exposes an **Info** link that opens the associated documentation.

### Model Unloading

GMOV supports:

- Unloading individual Models
- Unloading Model Assemblies
- Unloading all loaded Models

---

## Typical Workflow

### 1. Load Models

Load one or more FDOs into GMOV.

### 2. Inspect Available Services

Review Services exposed by the loaded Models.

### 3. Execute Services

Run Services directly or invoke them through the Agent interface.

### 4. Review Results

Inspect execution results and provenance.

### 5. Unload Models

Remove Models or Assemblies from the environment when no longer needed.

---

## Example Interaction

User request:

```text
What is the recommendation for codeine for a poor CYP2D6 metabolizer?
```

Agent behavior:

```text
Identify candidate Services
Select codeine recommendation Service
Validate phenotype input
Invoke Controller
Execute Service
Return result
```

Result:

```text
Selected Service:
CPIC Codeine Recommendation

Result:
Avoid codeine because reduced conversion to morphine may reduce efficacy.

Provenance:
Model: CPIC Codeine Recommendation CYP2D6
Service: Codeine Recommendation
```

---

## Design Goals

GMOV is intended to demonstrate:

- FAIR Digital Objects as computational components
- Models as agent-usable tools
- Constrained AI-enabled computation
- Explicit Service selection
- Computational provenance
- MVC-based orchestration of models and agents

---

## Current Status

GMOV is an experimental research prototype.

The project is intended to support demonstrations, architectural exploration, and future research into AI-enabled interaction with FAIR Digital Objects and computable knowledge resources.

It should not be considered production software.
