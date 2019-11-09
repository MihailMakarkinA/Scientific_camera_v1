try:
    #import path to SDK DLL
    from windows_setup import configure_path
    configure_path()
except ImportError:
    configure_path = None

from pyqtgraph.Qt import QtCore, QtGui
import numpy as np
import pyqtgraph as pg
import pyqtgraph.ptime as ptime
from PyQt5.QtCore import Qt, QThread, QTimer
from PyQt5.QtWidgets import QMainWindow, QWidget, QApplication, QPushButton, QVBoxLayout, QSlider
from pyqtgraph import ImageView

from thorlabs_tsi_sdk.tl_camera import TLCameraSDK, TLCamera, Frame
from thorlabs_tsi_sdk.tl_camera_enums import SENSOR_TYPE
from thorlabs_tsi_sdk.tl_mono_to_color_processor import MonoToColorProcessorSDK

import sys
import threading
import queue
from PIL import Image

class GUI(pg.ImageView,QMainWindow):
    def __init__(self, image_queue):
        # type: (typing.Any, queue.Queue) -> GUI
        self.image_queue = image_queue
        self._image_width = 0
        self._image_height = 0
        self._get_image()

    app = QtGui.QApplication([])

    win = pg.GraphicsLayoutWidget()
    win.show()
    win.setWindowTitle('Scientific camera')
    view = win.addViewBox()
    view.setAspectLocked(True)
    img = pg.ImageItem(border='w')
    view.addItem(img)
    view.setRange(QtCore.QRectF(0, 0, 600, 600))

    i = 0

    updateTime = ptime.time()
    fps = 0



    def _get_image(self):
        global img, _image, i, j, updateTime, fps
        ## Display the data
        try:
            image = self.image_queue.get_nowait()
            _image = np.asarray(image, dtype='uint8')
            if (self._image.width() != self._image_width) or (self._image.height() != self._image_height):
                # resize the canvas to match the new image size
                self._image_width = self._image.width()
                self._image_height = self._image.height()
                self.config(width=self._image_width, height=self._image_height)
        except queue.Empty:
            pass
        QTimer.singleShot(5, self._get_image)

        img.setImage(_image[i])
        i = (i + 1) % _image.shape[0]

        QtCore.QTimer.singleShot(1, self._get_image)
        now = ptime.time()
        fps2 = 1.0 / (now - updateTime)
        updateTime = now
        fps = fps * 0.9 + fps2 * 0.1

class ImageAcquisitionThread(threading.Thread):

    def __init__(self, camera):
        # type: (TLCamera) -> ImageAcquisitionThread
        super(ImageAcquisitionThread, self).__init__()
        self._camera = camera

        # setup color processing if necessary
        if self._camera.camera_sensor_type != SENSOR_TYPE.BAYER:
            # Sensor type is not compatible with the color processing library
            self._is_color = False
        else:
            self._mono_to_color_sdk = MonoToColorProcessorSDK()
            self._image_width = self._camera.image_width_pixels
            self._image_height = self._camera.image_height_pixels
            self._mono_to_color_processor = self._mono_to_color_sdk.create_mono_to_color_processor(
                SENSOR_TYPE.BAYER,
                self._camera.color_filter_array_phase,
                self._camera.get_color_correction_matrix(),
                self._camera.get_default_white_balance_matrix(),
                self._camera.bit_depth
            )
            self._is_color = True

        self._bit_depth = camera.bit_depth
        self._camera.image_poll_timeout_ms = 0  # Do not want to block for long periods of time
        self._image_queue = queue.Queue(maxsize=2)
        self._stop_event = threading.Event()

    def get_output_queue(self):
        # type: (type(None)) -> queue.Queue
        return self._image_queue

    def stop(self):
        self._stop_event.set()

    def _get_color_image(self, frame):
        # type: (Frame) -> Image
        # verify the image size
        width = frame.image_buffer.shape[1]
        height = frame.image_buffer.shape[0]
        if (width != self._image_width) or (height != self._image_height):
            self._image_width = width
            self._image_height = height
            print("Image dimension change detected, image acquisition thread was updated")
        # color the image. transform_to_24 will scale to 8 bits per channel
        color_image_data = self._mono_to_color_processor.transform_to_24(frame.image_buffer,
                                                                         self._image_width,
                                                                         self._image_height)
        color_image_data = color_image_data.reshape(self._image_height, self._image_width, 3)
        # return PIL Image object
        return Image.fromarray(color_image_data, mode='RGB')

    def _get_image(self, frame):
        # type: (Frame) -> Image
        # no coloring, just scale down image to 8 bpp and place into PIL Image object
        scaled_image = frame.image_buffer >> (self._bit_depth - 8)
        return Image.fromarray(scaled_image)

    def run(self):
        while not self._stop_event.is_set():
            try:
                frame = self._camera.get_pending_frame_or_null()
                if frame is not None:
                    if self._is_color:
                        pil_image = self._get_color_image(frame)
                    else:
                        pil_image = self._get_image(frame)
                    self._image_queue.put_nowait(pil_image)
            except queue.Full:
                # No point in keeping this image around when the queue is full, let's skip to the next one
                pass
            except Exception as error:
                print("Encountered error: {error}, image acquisition will stop.".format(error=error))
                break
        print("Image acquisition has stopped")
        if self._is_color:
            self._mono_to_color_processor.dispose()
            self._mono_to_color_sdk.dispose()



if (sys.flags.interactive != 1) or not hasattr(QtCore, 'PYQT_VERSION'):
     QtGui.QApplication.instance().exec_()
     with TLCameraSDK() as sdk:
        camera_list = sdk.discover_available_cameras()
        with sdk.open_camera(camera_list[0]) as camera:
            print("Generating app...")
            image_acquisition_thread = ImageAcquisitionThread(camera)
            camera_widget = GUI(image_queue=image_acquisition_thread.get_output_queue())
            print("Setting camera parameters...")
            camera.frames_per_trigger_zero_for_unlimited = 0
            camera.arm(2)
            camera.issue_software_trigger()

            print("Starting image acquisition thread...")
            image_acquisition_thread.start()
            print("Waiting for image acquisition thread to finish...")
            image_acquisition_thread.stop()
            image_acquisition_thread.join()
            print(image_acquisition_thread)
            print("Closing resources...")




