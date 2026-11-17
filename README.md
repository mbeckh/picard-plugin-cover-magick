# Cover Magick
![Latest Version](https://img.shields.io/github/v/tag/mbeckh/picard-plugin-cover-magick?sort=semver&filter=v*&style=flat-square&label=latest)
![Supports](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmbeckh%2Fpicard-plugin-cover-magick%2Frefs%2Fheads%2Fmain%2F.works_with.yml&query=%24.picard.from&prefix=picard+%3E%3D&style=flat-square&label=supports&color=orange)
![Tested with](https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fmbeckh%2Fpicard-plugin-cover-magick%2Frefs%2Fheads%2Fmain%2F.works_with.yml&query=%24.picard.latest&prefix=picard+%3C%3D&style=flat-square&label=tested+with&color=green)

A plugin for [MusicBrainz Picard](https://picard.musicbrainz.org) to scale covers using [ImageMagick](https://imagemagick.org).

## What it Does

ImageMagick provides more options, e.g. for handling color spaces.

The plugin disables the built-in processors for resizing and format conversion and instead uses ImageMagick.

ImageMagick provides more options e.g. to handle color spaces in a way that provides better results. One such setting, sampling factor, is exposed in the options UI.
Resizing algorithm (Lanczos) and contrast compensation are currently hard-coded.

Besides, this also circumvents two (suppposed) problems:
* When processing differs for tags and file targets things get "awkward" because Picard internally only handles one QImage for both targets.
* If property `CoverArtImage.can_be_processed` is false, metadata is currently not updated.

If _any other_ cover processor is detected, the plugin currently throws an exception so that no processor is silently ignored.

Processing respects the following options from Picard:
* Width, height, resize mode and format for both tag and file targets.
* Image quality setting

> [!IMPORTANT]
> This plugin requires the [Monkey Plugin](https://github.com/mbeckh/picard-plugin-monkey/) as a prerequisite. Please install this plugin first.

This plugin modifies Picard _way beyond_ the level that was intended by the implementers of the official API. It relies heavily on internal and private implementation details and monkey patches its way across the Picard code base to achieve its goals.

> [!WARNING]
> **Even a minor update of Picard can break this plugin** until I'm able to supply an updated version. The plugin is typically tested with the latest version of Picard only.

## License

The code is released under the Apache License Version 2.0. Please see [LICENSE](LICENSE) for details and [NOTICE](NOTICE) for the required information when using picard-plugin-cover-magick in your own work.
