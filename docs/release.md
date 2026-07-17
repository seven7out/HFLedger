# Release checklist

The repository is designed so all technical preparation can finish before choosing a publishing account. Creating a remote, pushing, tagging, and posting publicly are deliberate maintainer actions.

## Candidate verification

1. Read the complete tree for private data, machine paths, copied operational prose, credentials, and non-fictional fixtures.
2. Run the external denylist gate and the deterministic release contract:

   ```sh
   LEDGER_PUBLISH_GATE=/path/to/publish-gate.sh ./scripts/release-check
   ```

3. Confirm `git status --short` is empty.
4. Confirm `core.__version__`, [`CHANGELOG.md`](../CHANGELOG.md), and the intended tag agree.
5. Run the README demo from a fresh clone or clean worktree and make one real swipe.
6. Inspect the board and deck at desktop and phone widths with no console errors or horizontal overflow.

The privacy gate deliberately lives outside the public repository so the denylist cannot reveal the identities and systems it protects.

## Repository publication

After the maintainer chooses the publishing account and final repository slug:

```sh
git remote add origin git@github.com:ACCOUNT/REPOSITORY.git
git push -u origin main
git tag -a v0.4.0 -m "Ledger 0.4.0"
git push origin v0.4.0
```

Before making the repository public:

- keep GitHub secret scanning and private vulnerability reporting enabled;
- verify the default branch is `main` and require review for future protected changes;
- add the actual repository URL to clone instructions where appropriate;
- verify the MIT license is detected;
- create the `v0.4.0` release from [`CHANGELOG.md`](../CHANGELOG.md);
- clone the public repository into a new directory and run `scripts/release-check` there.

Do not publish a Ledger data directory, generated collector report, local instruction pack, scheduler file containing machine paths, or private privacy-gate denylist.

## Launch sequence

1. Publish the repository and release.
2. Re-run the two-minute demo from the public clone.
3. Post the Show HN copy in [`launch.md`](launch.md); stay available for technical questions.
4. Post the LocalLLaMA version only after answering early correctness or safety concerns in the repository.
5. Send the short blurb to agent-tooling roundups with the protocol and demo links.
6. Capture recurring questions as documentation or fictional examples rather than ad hoc claims.

## Rollback

If private data, credentials, or unsafe instructions appear, make the repository private immediately, remove public release artifacts, rotate any exposed credential, preserve the relevant commit hashes privately for incident analysis, and rebuild publication from a newly audited clean history. Deleting one Git commit is not sufficient once public clones or archives may exist.
