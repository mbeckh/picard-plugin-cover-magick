# Perform cover scaling using ImageMagick instead of Qt.
# This also circumvents a when processing differs for tags and file targets
# because Picard internally only handles one QImage.
# It also fixes a bug ignoring unprocessed covers in metadata.
#
# Copyright 2026 Michael Beckh (mbeckh)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Callable
from functools import partial
import os
import subprocess
import time
from typing import TYPE_CHECKING, ClassVar, Final, cast, override

from PyQt6.QtWidgets import QFileDialog

from picard.const.cover_processing import (
    COVER_PROCESSING_SLEEP,
    ImageFormat,
    ResizeModes,
)
from picard.const.sys import IS_WIN
from picard.coverart import (
    CoverArtImage,
    CoverArtImageProcessing,
)
from picard.coverart.processing import (
    CoverArtProcessingError,
    handle_processing_exceptions,
)
from picard.coverart.processing.processors import (
    ConvertImage,
    ResizeImage,
)
from picard.plugin3.api import (
    ImageProcessor,
    OptionsPage,
    PageOptionConfigs,
    PluginApi,
    ProcessingImage,
    t_,
)
from picard.util import thread
from picard.util.imageinfo import (
    ImageInfo,
    identify,
)

from . import monkey
from .forms.ui_options import Ui_CoverMagickOptionsPage


if IS_WIN or TYPE_CHECKING:
    import winreg

_api: PluginApi
_enabled = False


def find_magick() -> str:
    """Find magick binary."""

    if IS_WIN:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE') as root:
                with winreg.OpenKey(root, 'ImageMagick') as parent:
                    with winreg.OpenKey(parent, 'Current') as key:
                        (path, type) = winreg.QueryValueEx(key, 'BinPath')
                        if type != winreg.REG_SZ:
                            _api.logger.warning("BinPath has wrong type %d", type)
                            return 'magick.exe'
                        _api.logger.debug("ImageMagick found in %r", path)
                        return os.path.join(path, 'magick.exe')
        except Exception:
            _api.logger.warning("Error reading path of magick.exe", exc_info=True)
        return 'magick.exe'
    else:
        return 'magick'


def magick_run(
    image_data: bytes, target_width: int, target_height: int, mode: ResizeModes, format: ImageFormat
) -> bytes:
    """Scale and/or convert image data using magick."""

    setting = _api.global_config.setting
    plugin_config = _api.plugin_config

    magick = plugin_config['magick_command']

    command = [
        magick,
        '-',
    ]

    width = int(target_width)
    height = int(target_height)
    if width != 0 and height != 0:
        match mode:
            case ResizeModes.MAINTAIN_ASPECT_RATIO:
                size = f'{width:d}x{height:d}'
            case ResizeModes.SCALE_TO_WIDTH:
                size = f'{width:d}x'
            case ResizeModes.SCALE_TO_HEIGHT:
                size = f'x{height:d}'
            case ResizeModes.CROP_TO_FIT:
                size = f'{width:d}x{height:d}^'
            case ResizeModes.STRETCH_TO_FIT:
                size = f'{width:d}x{height:d}!'
            case _:
                _api.logger.error("Unknown resize mode: %r", mode)
                size = f'{width:d}x{height:d}'
        # fmt: off
        command += [
            '-colorspace', 'RGB',
            '+sigmoidal-contrast', '6.5,50%',
            '-filter', 'Lanczos',
            '-distort', 'resize', size,
            '-sigmoidal-contrast', '6.5,50%',
            '-colorspace', 'sRGB',
        ]
        # fmt: on

    # fmt: off
    command += [
        '-strip',
        '-density', '96',
        '-quality',
        '{:d}'.format(setting['cover_image_quality']),
        '-interlace', 'none',
        '-sampling-factor', plugin_config['cover_sampling_factor'],
        f'{format.name}:-',
    ]
    # fmt: on

    si = None
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    result = subprocess.run(command, input=image_data, stdout=subprocess.PIPE, check=True, startupinfo=si).stdout

    return result


#
# Disable built-in processors
#


@monkey.patch(ResizeImage, ResizeImage.target)
def _magick_ResizeImage_target(wrapped, self: ResizeImage):
    """Disable ResizeImage."""

    if _enabled:
        return ImageProcessor.Target.NONE
    return wrapped(self)


@monkey.patch(ConvertImage, ConvertImage.target)
def _magick_ConvertImage_target(wrapped, self: ConvertImage):
    """Disable ConvertImage."""

    if _enabled:
        return ImageProcessor.Target.NONE
    return wrapped(self)


@monkey.patch(CoverArtImageProcessing, CoverArtImageProcessing._run_processors_queue)
@handle_processing_exceptions
def _magick_CoverArtImageProcessing_run_processors_queue(
    wrapped,
    self: CoverArtImageProcessing,
    coverartimage: CoverArtImage,
    initial_data: bytes,
    start_time: int | float,
    save_images_to_tags: bool,
    save_images_to_files: bool,
    image: ProcessingImage,
    target: ImageProcessor.Target,
) -> None:
    """Special processor to scale and convert using image magick.
    Prevents use of QImage as intermediate store in regular cover art processor.
    """

    if not _enabled:
        wrapped(self, coverartimage, initial_data, start_time, save_images_to_tags, save_images_to_files, image, target)
        return

    TAGS: Final[str] = 'tags'
    FILE: Final[str] = 'file'

    setting = _api.global_config.setting

    image_info = identify(initial_data)

    target_width = {TAGS: 0, FILE: 0}
    target_height = {TAGS: 0, FILE: 0}
    resize_mode = {TAGS: ResizeModes.MAINTAIN_ASPECT_RATIO, FILE: ResizeModes.MAINTAIN_ASPECT_RATIO}
    format = {TAGS: image_info.format_info, FILE: image_info.format_info}

    for type in [TAGS, FILE]:
        if type == TAGS and save_images_to_tags or type == FILE and save_images_to_files:
            if setting[f'cover_{type}_resize']:
                width = int(setting[f'cover_{type}_resize_target_width'])
                height = int(setting[f'cover_{type}_resize_target_height'])
                width_scale_factor = width / image_info.width
                height_scale_factor = height / image_info.height

                mode = setting[f'cover_{type}_resize_mode']
                if mode == ResizeModes.MAINTAIN_ASPECT_RATIO:
                    scale_factor = min(width_scale_factor, height_scale_factor)
                elif mode == ResizeModes.SCALE_TO_WIDTH:
                    scale_factor = width_scale_factor
                elif mode == ResizeModes.SCALE_TO_HEIGHT:
                    scale_factor = height_scale_factor
                else:  # crop or stretch
                    scale_factor = max(width_scale_factor, height_scale_factor)

                scale = scale_factor < 1 or scale_factor > 1 and setting[f'cover_{type}_enlarge']
                if scale:
                    target_width[type] = width
                    target_height[type] = height
                    resize_mode[type] = mode

            if setting[f'cover_{type}_convert_images']:
                format[type] = setting[f'cover_{type}_convert_to_format']

    def do_scale():
        if not (save_images_to_tags or save_images_to_files):
            # nothing to do
            coverartimage.set_data(initial_data)
            return

        if (
            target_width[TAGS] == target_width[FILE]
            and target_height[TAGS] == target_height[FILE]
            and format[TAGS] == format[FILE]
        ):
            if target_width[TAGS] == 0 and target_height[TAGS] == 0 and format[TAGS] == image_info.format_info:
                if target == ImageProcessor.Target.SAME:
                    # NO processing required for both TAGS and FILE
                    coverartimage.set_data(initial_data)
                return

            if resize_mode[TAGS] == resize_mode[FILE]:
                if target == ImageProcessor.Target.SAME:
                    # one processing
                    new_data = magick_run(
                        initial_data,
                        target_width[TAGS],
                        target_height[TAGS],
                        resize_mode[TAGS],
                        cast(ImageFormat, format[TAGS]),
                    )
                    coverartimage.set_data(new_data)
                return

            type = (
                TAGS if target == ImageProcessor.Target.TAGS else FILE if target == ImageProcessor.Target.FILE else None
            )
            if type == TAGS and save_images_to_tags or type == FILE and save_images_to_files:
                if target_width[type] == 0 and target_height[type] == 0 and format[type] == image_info.format_info:
                    # NO processing required for type
                    if type == TAGS and save_images_to_tags:
                        coverartimage.set_data(initial_data)
                    if type == FILE and save_images_to_files:
                        # set external data because tag data is different
                        coverartimage.set_external_file_data(initial_data)
                    return

                # separate processing
                new_data = magick_run(
                    initial_data,
                    target_width[type],
                    target_height[type],
                    resize_mode[type],
                    cast(ImageFormat, format[type]),
                )
                if type == TAGS and save_images_to_tags:
                    coverartimage.set_data(new_data)
                elif type == FILE and save_images_to_files:
                    coverartimage.set_external_file_data(new_data)

    do_scale()

    _api.logger.debug(
        "Magick scaling for %s cover art image %s finished in %d ms",
        target.name,
        coverartimage,
        1000 * (time.time() - start_time),
    )
    time.sleep(COVER_PROCESSING_SLEEP)

    # Check for other processors
    # The point of this processor is to NOT use QImage, so other processors do not make any sense.
    queue = self.queues[target]
    if queue:
        for processor in queue:
            if processor.target() != ImageProcessor.Target.NONE:
                raise CoverArtProcessingError(f"Covert art processor {processor!r} did not run")


@monkey.patch(CoverArtImageProcessing, CoverArtImageProcessing.run_image_processors)
def _magick_CoverArtImageProcessing_run_image_processors(
    wrapped,
    self: CoverArtImageProcessing,
    coverartimage: CoverArtImage,
    initial_data: bytes,
    image_info: ImageInfo,
    callback: Callable[[CoverArtImage, Exception | None], None],
) -> None:
    """Fixes bug that unprocessed covers are not updated in metadata.
    While original code looks ok, it  only works if set_data is called from thread (for reasons unknown).
    """

    if not _enabled:
        wrapped(self, coverartimage, initial_data, image_info, callback)
        return

    if coverartimage.can_be_processed:
        run_processors = partial(self._run_image_processors, coverartimage, initial_data, image_info)

        def next_func(result=None, error=None):
            callback(coverartimage, error)

        thread.run_task(run_processors, next_func=next_func, task_counter=self.task_counter)
    else:
        # BEGIN FIX
        def next_func(result=None, error=None):
            callback(coverartimage, error)

        thread.run_task(
            partial(coverartimage.set_data, initial_data), next_func=next_func, task_counter=self.task_counter
        )
        # END FIX
        # coverartimage.set_data(initial_data)
        # callback(coverartimage, None)


class CoverMagickOptionsPage(OptionsPage):
    """Options page."""

    NAME = 'cover_magick'
    TITLE = t_('ui.cover_magick.title', "Cover Magick")
    PARENT = 'plugins'

    OPTIONS: ClassVar[PageOptionConfigs] = {
        'magick_command': {'widgets': ['magick_command_edit']},
        'cover_sampling_factor': {'widgets': ['cover_sampling_factor']},
    }

    @override
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_CoverMagickOptionsPage()
        self.ui.setupUi(self)

        self.ui.cover_sampling_factor.addItems(
            [
                '1x1,1x1,1x1',
                '2x1,1x1,1x1',
                '2x2,1x1,1x1',
            ]
        )

        self.ui.magick_command_browse.clicked.connect(self._magick_command_browse)

    @override
    def load(self):
        plugin_config = self.api.plugin_config

        self.ui.magick_command_edit.setText(plugin_config['magick_command'])
        self.ui.cover_sampling_factor.setCurrentText(plugin_config['cover_sampling_factor'])

    @override
    def save(self):
        plugin_config = self.api.plugin_config

        plugin_config['magick_command'] = self.ui.magick_command_edit.text()
        plugin_config['cover_sampling_factor'] = self.ui.cover_sampling_factor.currentText()

    def _magick_command_browse(self):
        path, _unused = QFileDialog.getOpenFileName(
            self, self.api.tr('ui.magick_browse.title', "Select magick executable"), self.ui.magick_command_edit.text()
        )
        if path:
            path = os.path.normpath(path)
            self.ui.magick_command_edit.setText(path)


def enable(api: PluginApi):
    """Enable plugin."""

    global _api, _enabled

    _api = api
    _enabled = True

    api.plugin_config.register_option(
        'magick_command',
        default=find_magick(),
        title=api.tr('options.magick_command', "Path of magick command line utility"),
        in_profile=True,
    )
    api.plugin_config.register_option(
        'cover_sampling_factor',
        default='2x2,1x1,1x1',
        title=api.tr('options.cover_sampling_factor', "Sampling factor for scaled covers"),
        in_profile=True,
    )

    api.register_options_page(CoverMagickOptionsPage)


def disable():
    """Disable plugin."""

    global _api, _enabled

    _enabled = False
    monkey.unpatch_module(__name__)

    del _api
