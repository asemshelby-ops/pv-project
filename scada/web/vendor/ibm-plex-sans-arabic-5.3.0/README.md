# IBM Plex Sans Arabic 5.3.0, vendored

Used only by the story mode of the rooftop SIMULATION page (`scada/web/rooftop_story.js`,
`@font-face` in `scada/web/style.css`) for the Arabic captions. Vendored, not loaded from a
font service, because the SCADA must work offline on the lab PC.

Source: the npm package `@fontsource/ibm-plex-sans-arabic@5.3.0`
(integrity `sha512-ZZ4g+JDgQY0NqSh4o0MNWU1sHQmtcee1g++aKAwyJvZxnT5y51JXsN1cIXnaQaMd40dcbK6QFYa+Z1LbaiNuqA==`).
Font: IBM Plex Sans Arabic, Copyright 2019 IBM Corp., SIL Open Font License 1.1.
Files copied unchanged from the package's `files/` folder (weights 400 and 600, the
Arabic and the Latin subset):

| File here | sha256 |
|---|---|
| `ibm-plex-sans-arabic-arabic-400-normal.woff2` | `6010e7fd0dce5d527583951750728cbe3c895efbd16aa0f809ab8c824878c9d8` |
| `ibm-plex-sans-arabic-arabic-600-normal.woff2` | `16734a5adb27b0f363e566cbbeacec480da0dc0baa19c8f0053251c2e2bc0eac` |
| `ibm-plex-sans-arabic-latin-400-normal.woff2` | `9ed8dcb02e6c7246de4c120295fafa39a7bb73085f4951a4524a07dc911069f2` |
| `ibm-plex-sans-arabic-latin-600-normal.woff2` | `63f4757271e403f7baec0862f284ffaa4560ea6096ba9fe8bc6e585ca656e724` |
| `LICENSE.txt` (`LICENSE`, OFL-1.1) | `1c32ddc3fc6efd167cdcbe475195287f2dc26ed1a4c92fbe31c87ceaffe44f98` |

To upgrade: `npm pack @fontsource/ibm-plex-sans-arabic@<version>`, copy the same files
into a new folder, and change the paths in `scada/web/style.css` and `scada/server.py`.
