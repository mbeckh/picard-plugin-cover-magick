# Cover Magick

A plugin for [MusicBrainz Picard](https://picard.musicbrainz.org) to scale covers using [ImageMagick](https://imagemagick.org).

## What it Does

ImageMagick provides more options, e.g. for handling color spaces.

The plugin disables the built-in processors for resizing and format conversion and instead uses ImageMagick.

ImageMagick provides more options e.g. to handle color spaces in a way that provides better results. One such setting, sampling factor, is exposed in the options UI.
Resizing algorithm (Lanczos) and contrast compensation are currently hard-coded.

Besides, this also circumvents two (suppposed) problems:
* When processing differs for tags and file targets things get "awkward" because Picard internally only handles one QImage for both targets.
* If property `CoverArtImage.can_be_processed` is false, metadata is currently not updated.

If *any other* cover processor is detected, the plugin currently throws an exception so that no processor is silently ignored.

Processing respects the following options from Picard:
* Width, height, resize mode and format for both tag and file targets.
* Image quality setting

## License

The code is released under the Apache License Version 2.0. Please see [LICENSE](LICENSE) for details and [NOTICE](NOTICE) for the required information when using picard-plugin-cover-magick in your own work.
