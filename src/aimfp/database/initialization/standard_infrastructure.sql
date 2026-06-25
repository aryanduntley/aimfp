-- Standard Infrastructure Entries
-- Populated during project initialization with empty values
-- AI detects and populates values after initialization

INSERT INTO infrastructure (type, value, description) VALUES
  ('project_root', '', 'Full path to project root directory (e.g., /home/user/my-project)'),
  ('source_directory', '', 'Primary source code directory, RELATIVE to project_root (e.g., src). Stored relative so it resolves against the live worktree root; absolute values are auto-relativized.'),
  ('primary_language', '', 'Main programming language (e.g., Python 3.11, Rust 1.75, Node 18)'),
  ('build_tool', '', 'Primary build tool (e.g., cargo, npm, make, maven, gradle)'),
  ('package_manager', '', 'Package/dependency manager (e.g., pip, npm, cargo, maven)'),
  ('test_framework', '', 'Testing framework (e.g., pytest, jest, cargo test, junit)'),
  ('runtime_version', '', 'Language runtime or compiler version (e.g., Python 3.11.2, rustc 1.75.0)'),
  ('main_branch', 'main', 'Primary git branch name (main or master)');
