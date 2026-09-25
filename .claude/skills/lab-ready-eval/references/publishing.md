# Going public

## The hidden split must stay hidden

- The hidden generators live in a private git repository, loaded by the
  public tree through an environment variable with a sibling-directory
  default. The loader refuses a path inside the public repo, loads modules by
  path under a synthetic package so a private module cannot shadow a public
  one, and fails hidden generation with one clear line when the tree is
  absent. Public generation works without it.
- Anything hidden-only moves: seeds, parameter tables, compositions, helper
  functions used only by hidden cases. Generators that built both draws
  inline must take the draw as arguments. Grep the public tree afterwards for
  every hidden seed and hidden function name.
- Prove byte identity after the move: regenerate both splits into a throwaway
  tree and diff every case file and every referenced asset against the corpus
  of record.
- Back the private tree up to a private remote the same day. A tree on one
  disk makes every hidden number unreproducible if the disk is lost.
- Hidden case names in reports are acceptable; parameters are not.

## History

If the hidden generators were ever committed to the public repository, its
history leaks them, whatever the working tree says. Publish a fresh export,
never the working repository:

- Route A: `git archive` the release commit into a new single-commit
  repository.
- Route B: `git filter-repo` removing the generator paths and the internal
  directories, then re-add today's public generators.

Then verify: grep every commit of the export for each hidden module name and
each hidden seed; confirm the hidden corpus, run directories, credentials and
internal notes never appear in any commit. The maintainer runs the export and
the push; an agent prepares it in a scratch directory for inspection.

## The checklist

- [ ] Reference as candidate 1.0, controls green, tests passing, on the
      release commit.
- [ ] Every report carries numbers of record under the current metric;
      no projection anywhere; generated sections regenerated.
- [ ] Public docs in the maintainers' voice; internal notes under
      `docs/internal/`; the publishing checklist itself excluded.
- [ ] Private generator tree pushed to a private remote; the public export
      strips its history.
- [ ] No credential-shaped path in any commit; `.gitignore` covers run
      directories, caches, assets, hidden corpus.
- [ ] Placeholder links, contact addresses, and any site export refreshed to
      the numbers of record or clearly dated.
- [ ] The submission route for outsiders is documented: reproduce public
      numbers themselves; send a submission for hidden grading.
- [ ] Licenses: the project's, and the oracle's, stated in the README.
