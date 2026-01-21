"""Tests for settings configuration and source detection."""

import pytest
from cast2md.config.settings import (
    get_setting_source,
    NODE_SPECIFIC_SETTINGS,
    _DEFAULTS,
)


class TestSettingSource:
    """Tests for get_setting_source function."""

    def test_node_specific_with_env_override(self):
        """Node-specific settings from env file should show 'env_file'."""
        # whisper_model is node-specific, default is 'base'
        # If current value differs from default, it's from env file
        source = get_setting_source("whisper_model", "large-v3-turbo", None)
        assert source == "env_file"

    def test_node_specific_with_default(self):
        """Node-specific settings at default value should show 'default'."""
        source = get_setting_source("whisper_model", "base", None)
        assert source == "default"

    def test_node_specific_ignores_db_value(self):
        """Node-specific settings should ignore DB values and report env_file."""
        # Even if there's a DB value, node-specific settings come from env
        source = get_setting_source("whisper_model", "large-v3-turbo", "medium")
        assert source == "env_file"

    def test_node_specific_with_default_ignores_db(self):
        """Node-specific at default should show 'default' even with DB value."""
        source = get_setting_source("whisper_model", "base", "large-v3-turbo")
        assert source == "default"

    def test_regular_setting_from_database(self):
        """Regular settings with DB override should show 'database'."""
        source = get_setting_source("stuck_threshold_hours", 4, "4")
        assert source == "database"

    def test_regular_setting_from_env_file(self):
        """Regular settings from env file should show 'env_file'."""
        # stuck_threshold_hours default is 2
        source = get_setting_source("stuck_threshold_hours", 5, None)
        assert source == "env_file"

    def test_regular_setting_at_default(self):
        """Regular settings at default should show 'default'."""
        source = get_setting_source("stuck_threshold_hours", 2, None)
        assert source == "default"

    def test_all_node_specific_keys_in_defaults(self):
        """All node-specific keys should have default values defined."""
        for key in NODE_SPECIFIC_SETTINGS:
            assert key in _DEFAULTS, f"Missing default for node-specific key: {key}"


class TestNodeSpecificSettings:
    """Tests for NODE_SPECIFIC_SETTINGS constant."""

    def test_whisper_settings_are_node_specific(self):
        """Whisper settings should be node-specific."""
        assert "whisper_model" in NODE_SPECIFIC_SETTINGS
        assert "whisper_device" in NODE_SPECIFIC_SETTINGS
        assert "whisper_compute_type" in NODE_SPECIFIC_SETTINGS
        assert "whisper_backend" in NODE_SPECIFIC_SETTINGS

    def test_non_whisper_settings_not_node_specific(self):
        """Non-whisper settings should not be node-specific."""
        assert "stuck_threshold_hours" not in NODE_SPECIFIC_SETTINGS
        assert "ntfy_enabled" not in NODE_SPECIFIC_SETTINGS
        assert "distributed_transcription_enabled" not in NODE_SPECIFIC_SETTINGS
