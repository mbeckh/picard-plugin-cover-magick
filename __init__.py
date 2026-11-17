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

"""Perform cover scaling using ImageMagick instead of Qt.

This also circumvents a when processing differs for tags and file targets
because Picard internally only handles one QImage.
It also fixes a bug ignoring unprocessed covers in metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import os
from pathlib import Path
import subprocess
import time
from typing import TYPE_CHECKING, ClassVar, cast, override

from PyQt6.QtWidgets import QFileDialog, QWidget

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
from .ui_compiled.ui_options import Ui_CoverMagickOptionsPage


if TYPE_CHECKING:
    from collections.abc import Callable


if IS_WIN or TYPE_CHECKING:
    import winreg

_api: PluginApi
_enabled = False


def find_magick() -> str:
    """Find magick binary.

    Returns:
        Path for magick executable (Windows only).
    """
    if IS_WIN:
        try:
            with (
                winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE') as root,
                winreg.OpenKey(root, 'ImageMagick') as parent,
                winreg.OpenKey(parent, 'Current') as key,
            ):
                (path, reg_type) = winreg.QueryValueEx(key, 'BinPath')
                if reg_type != winreg.REG_SZ:
                    _api.logger.warning("BinPath has wrong type %d", reg_type)
                    return 'magick.exe'
                _api.logger.debug("ImageMagick found in %r", path)
                return str(Path(path) / 'magick.exe')
        except Exception:
            _api.logger.warning("Error reading path of magick.exe", exc_info=True)
        return 'magick.exe'
    return 'magick'


def magick_run(
    image_data: bytes, target_width: int, target_height: int, mode: ResizeModes, image_format: ImageFormat
) -> bytes:
    """Scale and/or convert image data using magick.

    Returns:
        The data for the processed image.
    """
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
        f'{image_format.name}:-',
    ]
    # fmt: on

    si = None
    if IS_WIN:
        si = subprocess.STARTUPINFO()
        si.dwFlags = subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = subprocess.SW_HIDE

    return subprocess.run(command, input=image_data, stdout=subprocess.PIPE, check=True, startupinfo=si).stdout


#
# Disable built-in processors
#


@monkey.patch(ResizeImage, ResizeImage.target)
def _monkey_ResizeImage_target(
    wrapped: Callable[[ResizeImage], ImageProcessor.Target], self: ResizeImage
) -> ImageProcessor.Target:
    """Disable ResizeImage.

    Returns:
        Always ImageProcessor.Target.NONE to disable processor.
    """
    if _enabled:
        return ImageProcessor.Target.NONE
    return wrapped(self)


@monkey.patch(ConvertImage, ConvertImage.target)
def _monkey_ConvertImage_target(
    wrapped: Callable[[ConvertImage], ImageProcessor.Target], self: ConvertImage
) -> ImageProcessor.Target:
    """Disable ConvertImage.

    Returns:
        Always ImageProcessor.Target.NONE to disable processor.
    """
    if _enabled:
        return ImageProcessor.Target.NONE
    return wrapped(self)


#
# Actual scaling.
#


@dataclass
class _ConvertTo:
    width: int
    height: int
    mode: ResizeModes
    format_info: ImageFormat


def _create_noop_convert_to(image_info: ImageInfo) -> _ConvertTo:
    """Create an empty class signalling that no conversion should take place.

    Returns:
        An empty _ConvertTo object.
    """
    return _ConvertTo(
        width=0,
        height=0,
        mode=ResizeModes.MAINTAIN_ASPECT_RATIO,
        format_info=cast('ImageFormat', image_info.format_info),
    )


def _get_target_parameters(target: str, image_info: ImageInfo) -> _ConvertTo:
    """Calculate image parameters for target.

    If no processing should take place, width and height are set to 0 and format_info is set to the input format.

    Returns:
        The processing parameters for target.
    """
    result = _create_noop_convert_to(image_info)

    setting = _api.global_config.setting
    if setting[f'cover_{target}_resize']:
        width = int(setting[f'cover_{target}_resize_target_width'])
        height = int(setting[f'cover_{target}_resize_target_height'])
        width_scale_factor = width / image_info.width
        height_scale_factor = height / image_info.height

        mode = setting[f'cover_{target}_resize_mode']
        if mode == ResizeModes.MAINTAIN_ASPECT_RATIO:
            scale_factor = min(width_scale_factor, height_scale_factor)
        elif mode == ResizeModes.SCALE_TO_WIDTH:
            scale_factor = width_scale_factor
        elif mode == ResizeModes.SCALE_TO_HEIGHT:
            scale_factor = height_scale_factor
        else:  # crop or stretch
            scale_factor = max(width_scale_factor, height_scale_factor)

        scale = scale_factor < 1 or (scale_factor > 1 and setting[f'cover_{target}_enlarge'])
        if scale:
            result.width = width
            result.height = height
            result.mode = mode

    if setting[f'cover_{target}_convert_images']:
        result.format_info = setting[f'cover_{target}_convert_to_format']

    return result


def _common_processing(
    initial_data: bytes, image_info: ImageInfo, target: ImageProcessor.Target, tags: _ConvertTo, file: _ConvertTo
) -> tuple[bool, bytes | None]:
    """Check if tags and file have the same image.

    Returns:
        A tuple with first argument True if tags and file share the same image. Data in second argument if target is SAME.
    """
    if tags.width == file.width and tags.height == file.height and tags.format_info == file.format_info:
        if tags.width == 0 and tags.height == 0 and file.format_info == image_info.format_info:
            if target == ImageProcessor.Target.SAME:
                # NO processing required for both TAGS and FILE
                return (True, initial_data)
            # processor called for separate processing, so we're done but do NOT set data
            return (True, None)

        if tags.mode == file.mode:
            if target == ImageProcessor.Target.SAME:
                # one processing
                new_data = magick_run(
                    initial_data,
                    tags.width,
                    tags.height,
                    tags.mode,
                    tags.format_info,
                )
                return (True, new_data)
            # processor called for separate processing, so we're done but do NOT set data
            return (True, None)
    # Check separate processing
    return (False, None)


def _separate_processing(initial_data: bytes, image_info: ImageInfo, convert_to: _ConvertTo) -> bytes:
    """Processing for either tags or file.

    Returns:
        The processed image data.
    """
    if convert_to.width == 0 and convert_to.height == 0 and convert_to.format_info == image_info.format_info:
        # NO processing required for type
        return initial_data

    # separate processing
    return magick_run(
        initial_data,
        convert_to.width,
        convert_to.height,
        convert_to.mode,
        convert_to.format_info,
    )


@monkey.patch(CoverArtImageProcessing, CoverArtImageProcessing._run_processors_queue)
@handle_processing_exceptions
def _monkey_CoverArtImageProcessing_run_processors_queue(  # ruff: ignore[too-many-positional-arguments]
    wrapped: Callable[
        [
            CoverArtImageProcessing,
            CoverArtImage,
            bytes,
            int | float,
            bool,
            bool,
            ProcessingImage,
            ImageProcessor.Target,
        ],
        None,
    ],
    self: CoverArtImageProcessing,
    coverartimage: CoverArtImage,
    initial_data: bytes,
    start_time: int | float,  # ruff: ignore[redundant-numeric-union] follows definition in CoverArtImageProcessing
    save_images_to_tags: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    save_images_to_files: bool,  # ruff: ignore[boolean-type-hint-positional-argument]
    image: ProcessingImage,
    target: ImageProcessor.Target,
) -> None:
    """Special processor to scale and convert using image magick.

    Prevents use of QImage as intermediate store in regular cover art processor.

    Raises:
        CoverArtProcessingError: If any other processor was suppressed.
    """
    if not _enabled:
        wrapped(self, coverartimage, initial_data, start_time, save_images_to_tags, save_images_to_files, image, target)
        return

    image_info = identify(initial_data)

    tags = _get_target_parameters('tags', image_info) if save_images_to_tags else _create_noop_convert_to(image_info)
    file = _get_target_parameters('file', image_info) if save_images_to_files else _create_noop_convert_to(image_info)

    if save_images_to_tags or save_images_to_files:
        done, data = _common_processing(initial_data, image_info, target, tags, file)
        if done:
            if data:
                coverartimage.set_data(data)
        else:
            convert_to, set_data = (
                (tags, coverartimage.set_data)
                if target == ImageProcessor.Target.TAGS and save_images_to_tags
                else (file, coverartimage.set_external_file_data)
                if target == ImageProcessor.Target.FILE and save_images_to_files
                else (None, coverartimage.set_data)
            )

            if convert_to:
                data = _separate_processing(initial_data, image_info, convert_to)
                if data:
                    set_data(data)
    else:
        # nothing to do
        coverartimage.set_data(initial_data)

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
def _monkey_CoverArtImageProcessing_run_image_processors(  # ruff: ignore[too-many-positional-arguments]
    wrapped: Callable[
        [CoverArtImageProcessing, CoverArtImage, bytes, ImageInfo, Callable[[CoverArtImage, Exception | None], None]],
        None,
    ],
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

    def next_func(result=None, error=None) -> None:  # ruff: ignore[missing-type-function-argument, unused-function-argument]
        callback(coverartimage, error)

    if coverartimage.can_be_processed:
        run_processors = partial(self._run_image_processors, coverartimage, initial_data, image_info)

        thread.run_task(run_processors, next_func=next_func, task_counter=self.task_counter)
    else:
        # BEGIN FIX
        thread.run_task(
            partial(coverartimage.set_data, initial_data), next_func=next_func, task_counter=self.task_counter
        )
        if False:
            # END FIX
            coverartimage.set_data(initial_data)
            callback(coverartimage, None)


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
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ui = Ui_CoverMagickOptionsPage()
        self.ui.setupUi(self)

        self.ui.cover_sampling_factor.addItems([
            '1x1,1x1,1x1',
            '2x1,1x1,1x1',
            '2x2,1x1,1x1',
        ])

        self.ui.magick_command_browse.clicked.connect(self._magick_command_browse)

    @override
    def load(self) -> None:
        plugin_config = self.api.plugin_config

        self.ui.magick_command_edit.setText(plugin_config['magick_command'])
        self.ui.cover_sampling_factor.setCurrentText(plugin_config['cover_sampling_factor'])

    @override
    def save(self) -> None:
        plugin_config = self.api.plugin_config

        plugin_config['magick_command'] = self.ui.magick_command_edit.text()
        plugin_config['cover_sampling_factor'] = self.ui.cover_sampling_factor.currentText()

    def _magick_command_browse(self) -> None:
        path, _unused = QFileDialog.getOpenFileName(
            self, self.api.tr('ui.magick_browse.title', "Select magick executable"), self.ui.magick_command_edit.text()
        )
        if path:
            path = os.path.normpath(path)
            self.ui.magick_command_edit.setText(path)


def enable(api: PluginApi) -> None:
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


def disable() -> None:
    """Disable plugin."""
    global _api, _enabled

    _enabled = False
    monkey.unpatch_module(__name__)

    del _api
