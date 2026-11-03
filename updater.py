"""Updates von GVL Media Studio ueber GitHub Releases. Der Ablauf steckt in gvl_common.updater."""
from gvl_common.updater import STAGE, Updater, clean, is_newer, launch

import installer

REPO = "hamterfreund08/gvl-media-studio"
ASSET = "GVL-Media-Studio.zip"
OBS_FILES = (installer.SCRIPT, "loader.html")

_updater = Updater(REPO, ASSET, "GVL-Media-Studio", installer.REQUIRED_FILES, OBS_FILES, installer.SETTINGS_DIR, installer.stop_service)
RELEASE_PAGE = _updater.release_page
latest = _updater.latest
download = _updater.download
start = _updater.start
apply = _updater.apply
take_result = _updater.take_result
