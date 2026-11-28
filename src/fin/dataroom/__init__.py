"""Data-room intake (SPEC-13, `fin dataroom`): scan a folder into a plan of what each file is.

- `unpack.py`: unpacks a .zip archive into its own new folder, or refuses it (an entry leaving the
  folder, a link, a password, over `[dataroom] archive_limit_mb`) with nothing written.
- `kinds.py`: names a file's kind from its type and the column profile whose headers it has, or
  gives the reason fin cannot use it and one next step.
- `scan.py`: walks the folder, drops duplicates by content hash, and stores the plan
  (`dataroom_files`); `read_plan` and `format_plan` show it. Nothing is loaded.
"""
