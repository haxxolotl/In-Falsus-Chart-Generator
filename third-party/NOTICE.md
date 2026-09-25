# Third-party components

AddressablesToolsPy 1.1.1 is vendored as a pure-Python catalog reader under MIT.
Source: https://github.com/anosu/AddressablesToolsPy
Its license is included here. Optional Rust acceleration is not bundled.

The portable application includes Python and packages identified by
`build-receipt.json`. The builder copies each installed package's license
files with its metadata into `runtime/Lib/site-packages` in the release.
Keep those files when repackaging; their licenses are separate from this
project's MIT license.

FFmpeg 7.1 is an independent executable supplied by imageio-ffmpeg, invoked
as a subprocess. This build is GPL version 3 or later; its license is included.
FFmpeg source and build information: https://ffmpeg.org/download.html and
https://github.com/imageio/imageio-binaries/tree/master/ffmpeg
Before redistributing a portable release, accompany its exact FFmpeg build
with the corresponding source/build material required by that license.

No proprietary game assembly, soundtrack, stock chart or downloaded reference
chart is included in this project or the portable app.
