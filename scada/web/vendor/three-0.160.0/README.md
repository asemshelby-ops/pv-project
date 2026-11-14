# Three.js 0.160.0, vendored

Used only by the 3D view of the rooftop SIMULATION page (`scada/web/rooftop3d.js`).
Vendored, not loaded from a CDN, because the SCADA must work offline on the lab PC
(docs/scada/architecture.md, section 6).

Source: the npm package `three@0.160.0`
(integrity `sha512-DLU8lc0zNIPkM7rH5/e1Ks1Z8tWCGRq6g8mPowdDJpw1CFBJMU7UoJjC6PefXW7z//SSl0b2+GCw14LB+uDhng==`).
Files copied unchanged:

| File here | File in the package | sha256 |
|---|---|---|
| `three.module.min.js` | `build/three.module.min.js` | `3e690ac7d180b0aadf0891bea39eec643e29e2d3e75c99b18689518665f69ba6` |
| `OrbitControls.js` | `examples/jsm/controls/OrbitControls.js` | `5a44a9e86a2a0fb11933eed69bc2cd33c76a496854c1aed6ed776efa87d7b064` |
| `LICENSE.txt` | `LICENSE` (MIT) | `852e0e8699169bf9f6fdc6bda3e682d078dcbc738b5d33e74df594721bff271d` |

`OrbitControls.js` imports from `'three'`; the import map in `rooftop.html` points that
name at `three.module.min.js` in this folder. To upgrade: `npm pack three@<version>`, copy
the same two files into a new folder `three-<version>/`, and change the import map and
the paths in `scada/server.py` (`STATIC`).
