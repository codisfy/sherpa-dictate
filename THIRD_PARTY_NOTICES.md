# Third-party notices

Sherpa uses the following third-party software. These components remain under
their respective licenses; the MIT License in `LICENSE` applies only to
Sherpa's original code.

| Component | License | Project |
| --- | --- | --- |
| PySide6, Shiboken6, and Qt | LGPL-3.0-only, with GPL alternatives | <https://www.qt.io/qt-for-python> |
| NumPy | BSD-3-Clause and bundled component licenses | <https://numpy.org/> |
| sherpa-onnx | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| Silero VAD (bundled speech detector) | MIT | <https://github.com/snakers4/silero-vad> |
| Python-SoXR and libsoxr | LGPL-2.1-or-later, with bundled component licenses | <https://github.com/dofuuz/python-soxr> |
| python-sounddevice | MIT | <https://python-sounddevice.readthedocs.io/> |
| dbus-next | MIT | <https://github.com/altdesktop/python-dbus-next> |
| PyInstaller (build tooling) | GPL-2.0-or-later with a special exception for bundled applications | <https://pyinstaller.org/> |

Binary distributions include the license texts supplied by NumPy and
sherpa-onnx, the GNU Lesser General Public License v3 used for the Qt/PySide6
distribution, the licenses supplied by Python-SoXR, and the MIT license texts
for Silero VAD, python-sounddevice, and dbus-next.

Apart from the bundled Silero speech detector, speech-recognition and
text-to-speech models are downloaded separately at the
user's request and are not distributed with Sherpa. Each model remains subject
to the license and terms published by its upstream provider.
