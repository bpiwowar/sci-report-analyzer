from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("sci-report-analyzer")
except PackageNotFoundError:  # running from the sources without installing
    __version__ = "dev"
