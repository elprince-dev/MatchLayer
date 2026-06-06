# EditorConfig and consistent editor settings

## Introduction

This document explains how a project keeps basic text-formatting settings — which
character encoding to use, whether lines end in a line feed (LF) or a carriage
return / line feed (CRLF), how wide an indent is, and whether files end with a
newline — identical across every contributor's editor. The mechanism is a small
file named `.editorconfig` that most editors read automatically. It belongs in
the Foundation and tooling track because every other file in the repository is
shaped by these settings, and a mismatch produces noisy, confusing changes.

**Learning outcomes** — after reading this document you will be able to:

- Describe what EditorConfig is and which formatting settings it controls.
- Explain why a shared settings file prevents whitespace-only changes between contributors.
- Read an EditorConfig section header and understand which files it applies to.
- Locate the project's EditorConfig file and predict how it formats a given file type.

Prerequisites: [Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md).
That document explains the single-repository, root-configuration idea that this
file builds on; otherwise this is an early Foundation topic written for a reader
who has never seen an EditorConfig file before.

## Problem it solves

When several people edit the same codebase, their editors disagree about
invisible details: one inserts tab characters where another inserts spaces, one
saves files with Windows-style carriage return / line feed (CRLF) endings while
another uses line feed (LF), and some leave trailing spaces at the ends of lines.
None of that changes what the program does, but all of it changes the bytes in
the file.

The common prior approach was to write the rules in a contributing guide and
hope everyone configured their editor by hand. That approach fails in practice:

- A contributor whose editor defaults to tabs commits a file full of tabs, and the next person's editor "fixes" it back to spaces, producing a large change that touches every line but alters no logic.
- Line-ending differences make an entire file look rewritten in a code review, burying the one real change among hundreds of phantom ones.
- New contributors have no automatic signal about the intended style, so each one re-discovers it (or does not).

EditorConfig solves this by putting the settings in a file the editor reads on
its own, so the correct behavior is applied automatically rather than relying on
each person's memory.

## Mental model

Think of an EditorConfig file as a dress code posted at the entrance of a shared
workshop: everyone who walks in follows the same rules without being told
individually, and the rules can be stricter for specific rooms.

To predict how a file will be formatted, read the EditorConfig top to bottom:

1. Start at the `root = true` line. It declares this file the top-most one, so the editor stops looking in parent folders for more rules.
2. Read the `[*]` section. The pattern `*` matches every file, so these are the defaults: encoding, line ending, indent style, indent width, and the trailing-newline and trailing-whitespace rules.
3. Read each more specific section, such as `[*.py]`. Its pattern matches only some files, and its settings override the defaults for exactly those files.
4. For any given file, combine the matching sections: start from the `[*]` defaults, then apply every more specific section whose pattern matches, with later and more specific rules winning.

That top-to-bottom, general-to-specific reading is the whole model. A file type
with no special section falls back to the `[*]` defaults.

## How it works

EditorConfig is a file format and an editor feature that together apply text
formatting rules automatically. A file named `.editorconfig` sits in a project
folder and contains sections; each section has a heading that is a filename
glob (a pattern such as `*` or `*.py` where `*` matches any run of characters)
and a list of `key = value` settings beneath it.

A supporting editor, when it opens a file, searches upward from that file
through its parent folders, reading every EditorConfig file it finds, and stops
when it reaches one that declares `root = true`. It then merges all the matching
sections: the universal `[*]` section provides defaults, and more specific globs
layer overrides on top. The settings most projects set are a small, stable list:

- the character encoding, commonly an 8-bit Unicode Transformation Format (UTF-8);
- the line-ending style, either line feed (LF) or carriage return / line feed (CRLF);
- whether a final newline is inserted at end of file;
- whether trailing whitespace is trimmed on save;
- the indent style (spaces or tabs) and the indent width.

The format is intentionally tiny and language-agnostic, and broad editor support
(many editors read it natively, others through a plugin) is what makes it
effective: the rules travel with the repository instead of living in each
person's private configuration. EditorConfig governs only these editor-level
formatting concerns; deeper code style (import ordering, quote style, line
wrapping) is the job of dedicated formatters, which it complements rather than
replaces.

## MatchLayer Phase 1 usage

MatchLayer commits a single top-most EditorConfig file at the repository root,
`.editorconfig`, so every application and package inherits one set of editor
defaults.

Source: `.editorconfig`

```text
# EditorConfig — https://editorconfig.org
# Top-most EditorConfig file for the MatchLayer monorepo.
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space
indent_size = 2

[*.py]
indent_size = 4

[Makefile]
indent_style = tab
```

The `root = true` line marks this as the top-most EditorConfig file, so editors
stop searching parent folders here. The `[*]` section sets the repository-wide
defaults: 8-bit Unicode Transformation Format (UTF-8) encoding, line feed (LF)
line endings, a guaranteed final newline, trimmed trailing whitespace, and
two-space indentation. Two overrides follow: Python files (`[*.py]`) use
four-space indents to match the conventions of that language's ecosystem, and
the `[Makefile]` section switches to tab indentation because the Make tool
requires literal tab characters in its rules. Those defaults line up with the
formatters configured elsewhere in the repository, so an editor that honors this
file produces output the formatters will accept rather than immediately rewrite.

## Common pitfalls

- **Mistake:** Assuming every editor honors EditorConfig out of the box and never installing the plugin some editors require.
  **Symptom:** Files saved from that editor still use the wrong indent or line ending despite the committed `.editorconfig`, and reviews show whitespace-only changes from that one contributor.
  **Recovery:** Confirm the editor has native EditorConfig support or install its EditorConfig plugin, then reopen the file so the settings take effect.

- **Mistake:** Treating EditorConfig as a full code formatter and expecting it to reorder imports, normalize quotes, or wrap long lines.
  **Symptom:** Code style drifts even though the `.editorconfig` is present, because those concerns are outside what the format controls.
  **Recovery:** Keep EditorConfig for editor-level whitespace and encoding settings, and rely on the project's dedicated formatters for deeper style; make sure the two agree on indentation.

- **Mistake:** Adding a second `.editorconfig` deeper in the tree without realizing the nearest one wins for the files beneath it.
  **Symptom:** A subfolder formats differently from the rest of the repository, and contributors cannot tell why the same file type behaves inconsistently.
  **Recovery:** Remove the unintended nested file, or scope its sections deliberately and understand that the search stops at the first ancestor marked `root = true`.

- **Mistake:** Hand-editing a generated or vendored file and reformatting it to match the `[*]` defaults when its tooling expects something else (for example converting a Makefile's tabs to spaces).
  **Symptom:** The tool that consumes the file breaks — a Makefile with space indentation fails because Make demands tabs.
  **Recovery:** Respect the file-type override (the `[Makefile]` tab rule exists for this reason) and leave generated files to their generators.

## External reading

- [EditorConfig — official site and specification](https://editorconfig.org/)
- [Mozilla Developer Network: character encodings and the UTF-8 entry](https://developer.mozilla.org/en-US/docs/Glossary/UTF-8)
- [Make manual: rule syntax and the tab requirement](https://www.gnu.org/software/make/manual/html_node/Rule-Syntax.html)
