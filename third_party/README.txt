СТОРОННИЕ КОМПОНЕНТЫ Attentive Otter 1.0 BETA

Исходники в сжатом виде находятся в папке Source/third_party/sources передаваемого
комплекта. sources.json содержит точные версии, URL, размер и SHA256 каждого архива.
SHA256 сверены с PyPI/Qt там, где они опубликованы; для остальных архивов вычислены
при загрузке с указанных upstream-адресов. Архивы не являются Python wheels.
Лицензии и уведомления сохранены без правок в licenses/installed и licenses/upstream.
Уведомления извлечены также из исходных архивов, включая нативные библиотеки.

Основные компоненты и лицензии:
  Python 3.13.8 — PSF; отдельные встроенные компоненты имеют собственные лицензии.
  PyQt5 5.15.11 — GPLv3; PyQt5-sip 12.17.1 — BSD-2-Clause.
  Qt 5.15.2 — LGPLv3/GPLv3; сторонние части Qt имеют собственные лицензии.
  NumPy 2.2.6, SciPy 1.16.2, psutil 7.1.0 — BSD.
  OpenCV / opencv-python 4.12.0.88 — Apache-2.0; FFmpeg 4.4.6 — LGPL-2.1-or-later.
  Pillow 11.3.0 — MIT-CMU; piexif 1.1.3 — MIT; pywin32 311 — PSF.
  pillow-heif 1.1.1 — BSD-3-Clause (Python-обёртка).
  libheif 1.20.2, libde265 1.0.16 — LGPL-3.0-or-later;
  x265 4.1+1-1d117be — GPL-2.0-or-later (используется совместно на условиях GPLv3).
  PyInstaller 6.16.0 — GPLv2 с исключением для bootloader.
Полные условия определяются приложенными текстами, включая исключения и notices.

ВАЖНО: LICENSES_bundled.txt из wheel pillow-heif сохранён как получен от upstream,
но перечисляет устаревшие версии. Фактические версии проверены через libheif_info()
и указаны в sources.json; приложены исходники именно фактических версий.
Рецепты и патчи сборки HEIC: pillow-heif-v1.1.1-upstream.tar.gz/libheif и workflows.
x265 зафиксирован по коммиту 1d117be, который показывает установленный encoder.

Qt: приложены модули qtbase, qtdeclarative, qtimageformats, qtsvg, qtwebsockets
версии 5.15.2, соответствующие DLL и плагинам сборки; инструкции — README/INSTALL
в архивах. Полные заголовки и исходники сторонних частей находятся в этих архивах.
Ресурсы Qt не менялись; библиотеки поставляются отдельными DLL.

FFmpeg: приложены исходники 4.4.6, libvpx 1.15.2, libaom 3.12.1, OpenH264 1.8.0,
а также точный upstream-коммит сборочных скриптов OpenCV (см. sources.json).
Внутри opencv-ffmpeg-build-recipes.tar.gz: ffmpeg/download_src.sh, make_mingw.sh,
docker/, патчи, plugin wrapper и параметры сборки. MD5 поставляемой DLL сверён
с 3rdparty/ffmpeg/ffmpeg.cmake из OpenCV 4.12.0; SHA256 указан в sources.json.
OpenH264 DLL отдельно в комплект не добавляется; поддержка в FFmpeg динамическая.

NumPy/SciPy включают OpenBLAS и runtime-компоненты компилятора. Их уведомления,
включая GCC Runtime Library Exception и другие исключения, находятся в лицензиях
установленных wheels. Дополнительные notices (OpenSSL, zlib, libjpeg и т.д.) см.
в лицензиях Python/Pillow/OpenCV/Qt и исходных архивах.

Сборка самого приложения описана в README.txt папки Source. Библиотеки использованы
из опубликованных upstream wheels; нативная пересборка требует инструментов,
перечисленных в соответствующих исходниках. Системные библиотеки Windows и
компиляторы как исходники приложения не включены.
