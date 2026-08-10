-- Standard User Settings
-- Populated during project initialization with default values
-- User can modify via update_user_setting helper

INSERT OR IGNORE INTO user_settings (setting_key, setting_value, description, scope) VALUES
  ('backup_count', '3', 'Maximum number of backup zip files to retain', 'project'),
  ('backup_duration', '30', 'Days of project inactivity before auto-backup triggers on new session', 'project'),
  ('backup_interval_days', '7', 'Days since the LAST BACKUP before a scheduled backup is reported due on session start. Distinct from backup_duration, which measures inactivity and therefore never fires on an actively developed project. Set to 0 to disable scheduled backups.', 'project'),
  ('backup_path', '.aimfp-project', 'Directory to store backup zip files (relative to project root)', 'project');
