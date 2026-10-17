# Hermes Agent Integration

This directory contains the repository-owned Hermes Agent integration used by the first
real ACMM pilot.

The initial subject remains:

```text
Agent:    Hermes Agent v0.21.3
Model:    nemotron-3-nano:30b-cloud
Toolset:  acmm
```

The model is taken from the Experiment Contract. The adapter does not override the Hermes
provider by default, so the already-qualified Hermes/Ollama gateway configuration remains
the source of provider routing for the pilot.

## `acmm-tools` plugin

`acmm-tools/` is a minimal Hermes plugin that registers exactly two tools in the `acmm`
toolset:

```text
get_magic_number
add_numbers
```

`get_magic_number` does not hard-code the experimental value. It reads
`ACMM_MAGIC_NUMBER`, which the ACMM Hermes adapter injects from the prepared environment
fixture for each run.

The tool returns structured JSON:

```json
{"magic_number": 87}
```

`add_numbers` returns:

```json
{"result": 100}
```

This shape is intentionally easy to evaluate deterministically.

## Installing or mounting the plugin

Hermes discovers user plugins under its Hermes home directory, normally:

```text
~/.hermes/plugins/
```

The repository directory should appear to Hermes as:

```text
~/.hermes/plugins/acmm-tools/
```

For the pinned Docker deployment, prefer mounting this repository copy read-only into the
container rather than maintaining a second hand-edited copy. The exact host/container
paths depend on the existing Hermes Docker command or Compose file.

After the plugin is visible inside Hermes, enable it once:

```text
hermes plugins enable acmm-tools
```

Then verify that the `acmm` toolset exposes the two pilot tools before collecting data.

## Docker adapter mode

`hermes_adapter_config(docker_container="...")` makes ACMM launch Hermes through:

```text
docker exec -i --env ACMM_MAGIC_NUMBER=<fixture-value> <container> hermes ...
```

The adapter therefore does not need the magic number baked into the image or persistent
Hermes configuration.

The existing ACMM Hermes baseline routes inference through the already-qualified Windows
Ollama gateway and keeps `nemotron-3-nano:30b-cloud` fixed. Provider/model variation is not
part of this pilot.
