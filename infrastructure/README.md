# ACMM Infrastructure

This directory contains version-controlled infrastructure definitions for ACMM experiment
subjects. It is an installation package: the repository ships desired-state profiles,
ready-to-use deployment defaults, and managed agent extensions, while mutable deployed
state lives outside Git.

The default mutable runtime root is:

```text
~/acmm_runtime
```

The current managed agent is Hermes:

```text
infrastructure/
└── hermes/
    ├── profile.yaml
    ├── defaults/
    │   ├── compose.yaml
    │   └── env.defaults
    ├── plugins/
    │   └── acmm-tools/
    └── README.md
```

`profile.yaml` is the ACMM-relevant desired-state summary for Hermes. The `defaults/`
directory contains ready-to-deploy installation files used by `acmm-infra hermes init`.

The source and deployment roots are deliberately separate:

```text
~/acmm_repo
    version-controlled definitions

~/acmm_runtime/hermes
    initialized Hermes deployment
```

The same layout can later be extended with sibling agent directories such as
`infrastructure/agent_zero/` and `infrastructure/open_webui/`.
