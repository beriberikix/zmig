# Before the repository is ever made public

Status: a to-do list only. The repository is private, and **nothing here has been done**.
Nothing is to be done without the owner's approval. History is not to be rewritten now.

1. **Third-party email address in a test fixture.**
   - `grant.ramsay@hotmail.com` appears in the copyright header of an upstream Zephyr file
     kept verbatim in a test fixture. The files are
     `tests/fixtures/patch-methods/base/drivers/ethernet/eth_esp32.c` and
     `tests/fixtures/patch-methods/target/drivers/ethernet/eth_esp32.c`.
   - `scripts/publish-allow.txt` allows it for the private repository only.
   - Before going public, replace it with a placeholder, e.g. `<author>`, in
     the fixture files and in every commit that contains them. This needs a history
     rewrite.
   - The `git apply` fixtures depend on these files' exact bytes. Regenerate the patches,
     or check that the header lies outside every hunk's context, and rerun
     `tests/test_patch_methods.py`.
   - Then remove the allowlist entry, so the publish scan fails if the address comes back.
2. **Session-attribution URLs in commit messages.**
   - Every commit message ends with a `Claude-Session: https://claude.ai/code/session_…`
     line.
   - A public release needs those lines stripped from all commit messages. This is also a
     history rewrite; keep the `Co-Authored-By` lines unless the owner decides otherwise.
3. **After both rewrites:**
   - run `scripts/publish.sh --scan-only` over the whole history;
   - check that no `grant.ramsay` and no `Claude-Session:` remain (`git log --all -p | grep`);
   - check the new SHAs against a fresh bundle backup;
   - get the owner's approval before changing the repository's visibility.
