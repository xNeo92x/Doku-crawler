# Third-party components

DocHarbor's own source is MIT-licensed. Bundled libraries retain their original
licenses and copyright notices. Python and Qt are dynamically bundled in the
portable distribution; their shared libraries can be replaced with compatible
versions in `_internal`. Nothing in DocHarbor's license prohibits modification
or reverse engineering for debugging modifications to LGPL components.

- Python: PSF License. Source: https://www.python.org/downloads/source/
- PySide6, Shiboken6 and Qt 6: LGPL-3.0 / GPL / commercial licensing as applicable.
  The shared Qt Core, GUI, Widgets, DBus and supporting platform libraries in this
  build are unmodified official PySide6 wheel components. LGPL-3.0 and GPL-3.0
  texts are included in `licenses/`. Corresponding upstream source is available
  via https://download.qt.io/official_releases/QtForPython/ and
  https://download.qt.io/official_releases/qt/ (match the bundled version), and
  https://code.qt.io/cgit/pyside/pyside-setup.git/ and
  https://code.qt.io/cgit/qt/qtbase.git/ . Version information is in the bundled
  package metadata; the tested PySide6 release is 6.11.2.
- aiohttp: Apache-2.0 and MIT. https://github.com/aio-libs/aiohttp
- beautifulsoup4: MIT. https://www.crummy.com/software/BeautifulSoup/
- markdownify: MIT. https://github.com/matthewwithanm/python-markdownify
- soupsieve: MIT. https://github.com/facelessuser/soupsieve
- Other transitive dependencies: original package license/metadata notices
  are copied into `licenses/package-metadata/` for the portable build.

Optional Playwright is not included in the standard portable build. When
installed separately, it and Chromium retain their own licenses.
