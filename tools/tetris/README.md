# Tetris bricks for the contribution graph

Original generator that packs a GitHub contribution calendar into tetrominoes and draws a looping SVG. Pieces fall from above the grid and lock on the days they belong to. The resting frame is the real contribution graph, in GitHub's light and dark greens.

This does not replace the profile README or the snake animation. Nothing here edits `README.md`, `snake.yml`, or the snake `output` branch.

## Preview

`sample/` is KenYanL's calendar for 2025-10-12 through 2026-10-17 (168 contributions), generated locally. The daily workflow republishes fresh SVGs on the `tetris-output` branch.

## Generate

```bash
python3 tools/tetris/generate.py --input tools/tetris/sample/contributions.json --out /tmp/tetris
python3 tools/tetris/generate.py --username KenYanL --token "$GITHUB_TOKEN" --out dist
python3 -m unittest tools/tetris/test_brickfall.py
```

No third-party packages. The Action uses `GITHUB_TOKEN` and the GraphQL `contributionsCollection` query, for the same Sunday-to-Saturday window as the profile graph.

## Workflow

`.github/workflows/tetris.yml` runs on a daily cron (`41 7 * * *`) and on `workflow_dispatch`. It writes:

- `tetris-contrib.svg`
- `tetris-contrib-dark.svg`

to the `tetris-output` branch only.

## Paste into the README later

```html
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/KenYanL/KenYanL/tetris-output/tetris-contrib-dark.svg" />
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/KenYanL/KenYanL/tetris-output/tetris-contrib.svg" />
  <img alt="Contribution graph assembling from Tetris bricks" src="https://raw.githubusercontent.com/KenYanL/KenYanL/tetris-output/tetris-contrib.svg" />
</picture>
```

## How it is drawn

The profile grid is 7 rows by about 53 weeks. Those cells are packed into I, O, T, S, Z, L, and J tetrominoes, rotations included. `7 × columns` is not always divisible by 4, so the remainder becomes a tromino, domino, or single brick. Each cell keeps that day's contribution level. Days with activity get a small stud and a bevel; empty days stay flat GitHub squares. One CSS animation drops the pieces. If animation is disabled, the finished graph remains.

Colors are the contribution colors shipped on github.com: light `#eff2f5 #aceebb #4ac26b #2da44e #116329`, dark `#151b23 #033a16 #196c2e #2ea043 #56d364`.

## Prior art & credits

Similar projects exist. None of their code is copied or wrapped. This generator was written for this repo.

| Project | License | What it does | How this differs |
| --- | --- | --- | --- |
| [debba/gh-contributions-tetris-gif-maker](https://github.com/debba/gh-contributions-tetris-gif-maker) | MIT | Single 1×1 cells fall down each column into place. GIF only, Python/Pillow. | Whole tetrominoes (with smaller leftovers) lock into the real 7×53 grid. Pure SVG/CSS, light and dark. |
| [leereilly/contribution-graph-art](https://github.com/leereilly/contribution-graph-art) | none | Tetrominoes fall through the graph as decoration. They do not build it. | Pieces land on the exact cells of the contribution calendar and stay there until the loop resets. |
| [JuhaP2/contribution-tetris](https://github.com/JuhaP2/contribution-tetris) | MIT | A bot plays a real game on a 10×20 board. | The board is the contribution grid, not a separate Tetris well. |
| [arndom/github-contribution-tetris](https://github.com/arndom/github-contribution-tetris) | MIT, archived | Splits a graph into tetrominoes for a game you play in the browser. | The split is automatic and played back as a README SVG. No game loop to play. |
| [Sur1ive/contribution-fx](https://github.com/Sur1ive/contribution-fx) | MIT | Gravity, plus other effects: cells drop to the bottom of the column and float back. | Cells do not pile up at the bottom. Each brick locks in its real week and weekday. |

The idea that a contribution grid can be shown as falling blocks is shared with those projects. The packer, the LEGO studs, the SVG timeline, and the workflow are separate.
