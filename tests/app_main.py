"""NiceGUI main file for the simulated-user UI tests."""

from nicegui import ui

from sci_report_analyzer.main import setup

setup()
ui.run(reload=False, storage_secret="test")
