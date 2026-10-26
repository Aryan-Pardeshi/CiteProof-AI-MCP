# Vendored frontend libraries

These files are committed so the app does not load scripts from a CDN at runtime, which lets the
Content-Security-Policy in `frontend/vercel.json` allow scripts from this site only. Update a library by
replacing its file, recomputing the hash, and updating this table in the same commit.

| File | Package | Version | Source | License | SHA-256 |
|---|---|---|---|---|---|
| `dompurify-3.4.17.min.js` | `dompurify` | 3.4.17 | `npm pack dompurify@3.4.17`, file `package/dist/purify.min.js` | Apache-2.0 or MPL-2.0 (`dompurify-LICENSE.txt`) | `cd37898f9782adf54820c413191b5691803f0f137c799d6edcfd38523b4c2c31` |
| `marked-12.0.2.min.js` | `marked` | 12.0.2 | `npm pack marked@12.0.2`, file `package/marked.min.js` | MIT (`marked-LICENSE.md`) | `15fabce5b65898b32b03f5ed25e9f891a729ad4c0d6d877110a7744aa847a894` |
| `lucide-0.468.0.min.js` | `lucide` | 0.468.0 | `npm pack lucide@0.468.0`, file `package/dist/umd/lucide.min.js` | ISC (`lucide-LICENSE.txt`) | `3411692820cb8d47543f69496aa25fd603a358f4498046f41c508a5a3342210e` |

Verify with `sha256sum frontend/vendor/*.js`.
