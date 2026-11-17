# Code style

How the App's code is written: Python under `src/bmpt`, `scripts` and `tests`, JavaScript and CSS under
`src/bmpt/web` and `tests/js`. Upstream's engine (`src/ba_modding_toolkit`) and the vendored libraries
(`src/bmpt/web/vendor`) keep their own style and are not reformatted.

## Tools

- Python: [ruff](https://docs.astral.sh/ruff/) formats and lints (`pyproject.toml`, `[tool.ruff]`).
- JavaScript and CSS: [Biome](https://biomejs.dev/) formats and lints (`biome.jsonc`).
- `.editorconfig` sets indentation, line endings and the 120-column limit for editors.
- `scripts/check_style.sh` runs both in check mode; `--fix` formats and applies safe fixes, `--staged` checks only
  the files staged for commit. CI runs the full check on every push to `main` and on pull requests.

Run `scripts/check_style.sh --fix` before committing. Formatting changes go in their own commit, apart from changes
to behaviour.

## Comments

- A comment says why: the constraint, the reason for an unusual choice, what would break if the code were simpler.
  The code itself says what it does.
- No history in comments: no "used to", "no longer", "was changed", versions, dates, issue or tracker numbers, or
  who asked for what. Version control keeps the history; the comment describes the code as it is now.
- Keep comments short and next to the code they explain. A long design explanation belongs in a docstring or in
  `docs/`, not in a block of line comments.
- No commented-out code.
- Comments and docstrings are in English.

## Python

- Every public module, class, function and method has a docstring in the
  [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings): a one-line summary
  ending with a period, a blank line, then any detail, then the sections that carry information:

  ```python
  def find_core(path: Path, server: str) -> Core | None:
      """Find the official core bundle a mod file replaces.

      Args:
          path: The mod's bundle file.
          server: The game server whose catalog is searched.

      Returns:
          The matching core, or None when the catalog has no entry for it.

      Raises:
          CatalogError: The catalog could not be read.
      """
  ```

- Private helpers (`_name`) need a docstring only when their purpose is not obvious from the name and signature.
- Type annotations on public functions; the docstring does not repeat the types.
- Names: `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_CASE` for constants.

## JavaScript

- Exported functions carry a JSDoc block: a one-line summary, `@param` with a type for each parameter (one line
  each), `@returns` with a type, and a `@typedef` for option objects:

  ```js
  /**
   * Fit a label into a box, shrinking the font down to its minimum.
   * @param {string} text The label.
   * @param {number} width The box width in stage pixels.
   * @param {{max: number, min: number}} size The font size range.
   * @returns {{fs: number, lines: number}} The font size used and the number of lines.
   */
  export function fitLabel(text, width, size) {
  ```

- Names: `camelCase` for functions and variables, `PascalCase` for classes, `UPPER_CASE` for module constants.
  File names are `kebab-case.js`.

## Module size

- Keep a source file under about 1500 lines. When a file grows past that, split it along what its parts do.
- One module, one purpose: a module's docstring or opening comment states it in a sentence or two.
