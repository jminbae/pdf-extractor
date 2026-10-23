# 1.2.2 completion and deployment

User authorization: resolve remaining issues and deploy. Existing source PDFs,
user Markdown/assets and earlier project-generated folders remain untouched.

- [x] Verify rules, local paths, current source, remote repository and releases.
- [x] Identify all 16 previously unselected textless pages as decorative blank
  dividers using actual source renders and native content inspection.
- [x] Ship verified blank classification/OCR retry and 300dpi/native-image path.
- [x] Ship narrowly validated Kiwi physical-line/page-boundary recovery, with
  normal-word false-join counterexamples and raw/source-location provenance.
- [x] Reuse public PyMuPDF table refinement and HTML export for merged cells;
  require cell position, text, punctuation and numeric boundary preservation.
- [x] Run all regressions, actual frozen worker/main and isolated 396-page book.
- [x] Fix dependency notice collection and exact-version source/build links.
- [x] Commit source with a complete in-repository Git object database; preserve
  old objects/ACLs and use standard temporary object-store/pack operations only.
- [ ] Build 1.2.2 installer, publish source/tag/installer/checksum, verify the
  published bytes and update contract, install and verify the deployed version.

Do not turn partial OCR diagnostics into a claim of perfect recognition. Actual
results, timings, checksums and remaining input limitations belong in the final
verification record and HANDOFF, not a universal completeness claim.
