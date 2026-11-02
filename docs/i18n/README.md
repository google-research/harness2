# Documentation translations

English files in the repository root and directly under `docs/` are the source
of truth. Translations are grouped by locale so that adding a language does not
add language-suffixed files throughout the repository.

## Available translations

- [简体中文 (`zh-CN`)](zh-CN/README.md)

## Directory layout

```text
docs/i18n/
├── README.md
├── zh-CN/
│   ├── README.md
│   ├── CONTRIBUTING.md
│   ├── SETUP.md
│   ├── BENCHMARKS.md
│   └── EXPERIMENTS.md
└── <locale>/
    └── ...
```

Use a valid BCP 47 language tag for each directory, such as `ja`, `ko`,
`pt-BR`, or `zh-TW`.

## Add a language

1. Create `docs/i18n/<locale>/` with the same filenames as an existing locale.
2. Translate prose and image alternative text. Keep commands, environment
   variables, file paths, model IDs, and code examples unchanged.
3. Make links between translated documents stay inside the locale directory.
   Link shared assets and repository files with paths relative to that directory.
4. Add the language to the language selector in the root `README.md` and each
   translated `README.md`.
5. Add a link from each English source document to its translated counterpart.
6. Check that every relative link resolves before submitting the change.

Keep heading coverage aligned with the English source. When English behavior or
commands change, update every affected translation in the same change whenever
possible.
