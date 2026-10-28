# Contributing

Thanks for wanting to help. Umbra records a person's whole day, so the bar is
not only "does it work" but "is it honest about what it knows, and does it keep
private things private".

## Before you start

- **A new data source or plugin** usually does not need to live in this
  repository. Both load from `~/.config/umbra/`, and the guides
  ([sources](docs/writing-a-source.md), [plugins](docs/writing-a-plugin.md))
  are written for exactly that. Open an issue if you think one belongs in the
  box for everyone.
- **A bug**: open an issue with `umbra doctor` output and what you expected.
  Leave out anything from your own timeline you would not post publicly.
- **Anything larger**: open an issue first, so the design can be agreed before
  you spend the time.

## Making a change

1. Read [AGENTS.md](AGENTS.md). It is written for coding agents, but it is the
   shortest accurate description of how the code fits together and which rules
   are easy to break. If you work with an agent, it will read it too.
2. Run the suite: `python3 -m unittest discover -s tests -t . -q`.
3. If you touched `web/src`, keep to [docs/design.md](docs/design.md) and
   [docs/ux.md](docs/ux.md), and work through the checklist at the end of
   `ux.md`. If you touched `web/src` or the API schema, run
   `bun run --cwd web build` and commit `umbra/web/` with your change.
4. If your change alters text that is sent to a model, say so in the PR: it
   invalidates every user's cache for that step.
5. Keep test fixtures synthetic. No real names, places, coordinates, screenshots
   of your own data, or transcripts of real conversations.

## Pull requests

One concern per PR. Describe the problem from the user's side, what you changed,
and how you checked it. Screenshots for UI changes, taken against
`python3 -m umbra.devseed` data rather than your own.

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE).
