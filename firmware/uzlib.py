"""PlatformIO pre-script: build the uzlib inflater bundled with the ESP8266 Arduino core.

It lives at framework-arduinoespressif8266/tools/sdk/uzlib/src (zlib-style licence, see its LICENSE).
Nothing is downloaded; this only adds those sources to the build.
"""
from os.path import isdir, join

Import("env")  # noqa: F821  (provided by PlatformIO)

framework = env.PioPlatform().get_package_dir("framework-arduinoespressif8266")  # noqa: F821
source = join(framework, "tools", "sdk", "uzlib", "src")
if not isdir(source):
    raise SystemExit(f"uzlib sources not found at {source}")

env.Append(CPPPATH=[source])  # noqa: F821
env.BuildSources(  # noqa: F821
    join("$BUILD_DIR", "uzlib"), source,
    src_filter=["-<*>", "+<tinflate.c>", "+<tinfzlib.c>", "+<adler32.c>", "+<crc32.c>"],
)
