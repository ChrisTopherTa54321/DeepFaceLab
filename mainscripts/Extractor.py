﻿import traceback
import os
import sys
import time
import multiprocessing
from tqdm import tqdm
from pathlib import Path
import numpy as np
import cv2
from utils import Path_utils
from utils.AlignedPNG import AlignedPNG
from utils import image_utils
from facelib import FaceType
import facelib
import gpufmkmgr

from utils.SubprocessorBase import SubprocessorBase
class ExtractSubprocessor(SubprocessorBase):

    #override
    def __init__(self, input_data, type, image_size, face_type, debug, multi_gpu=False, manual=False, manual_window_size=0, detector=None, output_path=None, input_path=None ):
        self.input_data = input_data
        self.type = type
        self.image_size = image_size
        self.face_type = face_type
        self.debug = debug
        self.multi_gpu = multi_gpu
        self.detector = detector
        self.output_path = output_path
        self.input_path = str(input_path)
        self.manual = manual
        self.manual_window_size = manual_window_size
        self.result = []

        no_response_time_sec = 60 if not self.manual else 999999
        super().__init__('Extractor', no_response_time_sec)

    #override
    def onHostClientsInitialized(self):
        if self.manual == True:
            self.wnd_name = 'Manual pass'
            cv2.namedWindow(self.wnd_name)

            self.landmarks = None
            self.param_x = -1
            self.param_y = -1
            self.param_rect_size = -1
            self.param = {'x': 0, 'y': 0, 'rect_size' : 5, 'rect_locked' : False, 'redraw_needed' : True, 'skipped': False }

            def onMouse(event, x, y, flags, param):
                if event == cv2.EVENT_MOUSEWHEEL:
                    mod = 1 if flags > 0 else -1
                    param['rect_size'] = max (5, param['rect_size'] + 10*mod)
                elif event == cv2.EVENT_LBUTTONDOWN:
                    param['rect_locked'] = not param['rect_locked']
                    param['redraw_needed'] = True
                elif not param['rect_locked']:
                    param['x'] = x
                    param['y'] = y

            cv2.setMouseCallback(self.wnd_name, onMouse, self.param)

    def get_devices_for_type (self, type, multi_gpu):
        if (type == 'rects' or type == 'landmarks'):
            if not multi_gpu:
                devices = [gpufmkmgr.getBestDeviceIdx()]
            else:
                devices = gpufmkmgr.getDevicesWithAtLeastTotalMemoryGB(2)
            devices = [ (idx, gpufmkmgr.getDeviceName(idx), gpufmkmgr.getDeviceVRAMTotalGb(idx) ) for idx in devices]

        elif type == 'final':
            devices = [ (i, 'CPU%d' % (i), 0 ) for i in range(0, multiprocessing.cpu_count()) ]

        return devices

    #override
    def process_info_generator(self):
        for (device_idx, device_name, device_total_vram_gb) in self.get_devices_for_type(self.type, self.multi_gpu):
            num_processes = 1
            if not self.manual and self.type == 'rects' and self.detector == 'mt':
                num_processes = int ( max (1, device_total_vram_gb / 2) )

            for i in range(0, num_processes ):
                device_name_for_process = device_name if num_processes == 1 else '%s #%d' % (device_name,i)
                yield device_name_for_process, {}, {'type' : self.type,
                                                    'device_idx' : device_idx,
                                                    'device_name' : device_name_for_process,
                                                    'image_size': self.image_size,
                                                    'face_type': self.face_type,
                                                    'debug': self.debug,
                                                    'output_dir': str(self.output_path),
                                                    'detector': self.detector}

    #override
    def get_no_process_started_message(self):
        if (self.type == 'rects' or self.type == 'landmarks'):
            print ( 'You have no capable GPUs. Try to close programs which can consume VRAM, and run again.')
        elif self.type == 'final':
            print ( 'Unable to start CPU processes.')

    #override
    def onHostGetProgressBarDesc(self):
        return None

    #override
    def onHostGetProgressBarLen(self):
        return len (self.input_data)

    #override
    def onHostGetData(self):
        if not self.manual:
            if len (self.input_data) > 0:
                return self.input_data.pop(0)
        else:
            skip_remaining = False
            allow_remark_faces = False
            while len (self.input_data) > 0:
                data = self.input_data[0]
                filename, faces = data

                is_frame_done = False
                go_to_prev_frame = False

                if self.param['redraw_needed']:
                    # New frame. Assume rect is locked and not skipped
                    self.param['rect_locked'] = False
                    self.param['skipped'] = False

                    # Update frame from file
                    self.original_image = cv2.imread(filename)

                    (h,w,c) = self.original_image.shape
                    self.view_scale = 1.0 if self.manual_window_size == 0 else self.manual_window_size / (w if w > h else h)
                    self.original_image = cv2.resize (self.original_image, ( int(w*self.view_scale), int(h*self.view_scale) ), interpolation=cv2.INTER_LINEAR)

                    self.text_lines_img = (image_utils.get_draw_text_lines ( self.original_image, (0,0, self.original_image.shape[1], min(100, self.original_image.shape[0]) ),
                                                    [   'Match landmarks with face exactly. Click to confirm/unconfirm selection',
                                                        '[Enter] - confirm and continue to next unmarked frame',
                                                        '[Space] - skip to next unmarked frame',
                                                        '[Mouse wheel] - change rect',
                                                        '[,] [.]- prev frame, next frame',
                                                        '[Q] - skip remaining frames'
                                                    ], (1, 1, 1) )*255).astype(np.uint8)
                    self.annotated_img = cv2.addWeighted (self.original_image,1.0,self.text_lines_img,1.0,0)

                    if faces and faces[0]:
                        # Can we mark an image that already has a marked face?
                        if allow_remark_faces:
                            prev_rect = faces.pop()[0]
                            self.param['rect_locked'] = True
                            faces.clear()
                            self.param['rect_size'] = ( prev_rect[2] - prev_rect[0] ) / 2
                            self.param['x'] = ( ( prev_rect[0] + prev_rect[2] ) / 2 ) * self.view_scale
                            self.param['y'] = ( ( prev_rect[1] + prev_rect[3] ) / 2 ) * self.view_scale
                    elif faces:
                        # A face of 'None' marks a skipped frame
                        self.param['skipped'] = True
                        faces.clear()

                (h,w,c) = self.original_image.shape
                if len(faces) == 0:
                    while True:
                        key = cv2.waitKey(1) & 0xFF

                        if key == ord('\r') or key == ord('\n'):
                            self.param['rect_locked'] = True
                            faces.append ( [(self.rect), self.landmarks] )
                            is_frame_done = True
                            break
                        elif key == ord(' '):
                            is_frame_done = True
                            self.param['skipped'] = True
                            faces.append( None )
                            break
                        elif key == ord('.') and len(self.input_data) > 1:
                            allow_remark_faces = True
                            # Only save the face if the rect is still locked
                            if self.param['rect_locked']:
                                faces.append ( [(self.rect), self.landmarks] )
                            elif self.param['skipped']:
                                faces.append( None )
                            is_frame_done = True
                            break
                        elif key == ord(',')  and len(self.result) > 0:
                            # Only save the face if the rect is still locked
                            if self.param['rect_locked']:
                                faces.append ( [(self.rect), self.landmarks] )
                            elif self.param['skipped']:
                                faces.append( None )
                            go_to_prev_frame = True
                            break
                        elif key == ord('q'):
                            skip_remaining = True
                            break

                        new_param_x = self.param['x'] / self.view_scale
                        new_param_y = self.param['y'] / self.view_scale
                        new_param_rect_size = self.param['rect_size']

                        new_param_x = np.clip (new_param_x, 0, w-1)
                        new_param_y = np.clip (new_param_y, 0, h-1)

                        if self.param_x != new_param_x or \
                           self.param_y != new_param_y or \
                           self.param_rect_size != new_param_rect_size or \
                           self.param['redraw_needed']:

                            self.param_x = new_param_x
                            self.param_y = new_param_y
                            self.param_rect_size = new_param_rect_size

                            self.rect = (self.param_x-self.param_rect_size, self.param_y-self.param_rect_size, self.param_x+self.param_rect_size, self.param_y+self.param_rect_size)
                            return [filename, [self.rect]]

                else:
                    is_frame_done = True

                if is_frame_done:
                    self.result.append ( data )
                    self.input_data.pop(0)
                    self.inc_progress_bar(1)
                    self.param['redraw_needed'] = True
                    self.param['rect_locked'] = False
                elif go_to_prev_frame:
                    self.input_data.insert(0, self.result.pop() )
                    self.inc_progress_bar(-1)
                    allow_remark_faces = True
                    self.param['redraw_needed'] = True
                    self.param['rect_locked'] = False
                elif skip_remaining:
                    while len(self.input_data) > 0:
                        self.result.append( self.input_data.pop(0) )
                        self.inc_progress_bar(1)

        return None

    #override
    def onHostDataReturn (self, data):
        if not self.manual:
            self.input_data.insert(0, data)

    #override
    def onClientInitialize(self, client_dict):
        self.safe_print ('Running on %s.' % (client_dict['device_name']) )
        self.type         = client_dict['type']
        self.image_size   = client_dict['image_size']
        self.face_type    = client_dict['face_type']
        self.device_idx   = client_dict['device_idx']
        self.output_path  = Path(client_dict['output_dir']) if 'output_dir' in client_dict.keys() else None
        self.debug        = client_dict['debug']
        self.detector     = client_dict['detector']

        self.keras = None
        self.tf = None
        self.tf_session = None

        self.e = None
        if self.type == 'rects':
            if self.detector is not None:
                if self.detector == 'mt':
                    self.tf = gpufmkmgr.import_tf ([self.device_idx], allow_growth=True)
                    self.tf_session = gpufmkmgr.get_tf_session()
                    self.keras = gpufmkmgr.import_keras()
                    self.e = facelib.MTCExtractor(self.keras, self.tf, self.tf_session)
                elif self.detector == 'dlib':
                    self.tf = gpufmkmgr.import_tf ([self.device_idx], allow_growth=True)
                    self.tf_session = gpufmkmgr.get_tf_session()
                    self.dlib = gpufmkmgr.import_dlib( self.device_idx )
                    self.e = facelib.DLIBExtractor(self.dlib)
                self.e.__enter__()

        elif self.type == 'landmarks':
            self.tf = gpufmkmgr.import_tf([self.device_idx], allow_growth=True)
            self.tf_session = gpufmkmgr.get_tf_session()
            self.keras = gpufmkmgr.import_keras()
            self.e = facelib.LandmarksExtractor(self.keras)
            self.e.__enter__()

        elif self.type == 'final':
            pass

        return None

    #override
    def onClientFinalize(self):
        if self.e is not None:
            self.e.__exit__()

    #override
    def onClientProcessData(self, data):
        filename_path = data[0]

        image = cv2.imread( filename_path )
        if image is None:
            print ( 'Failed to extract %s, reason: cv2.imread() fail.' % ( filename_path ) )
        else:
            if self.type == 'rects':
                rects = self.e.extract_from_bgr (image)
                return [filename_path, rects]

            elif self.type == 'landmarks':
                rects = data[1]
                landmarks = self.e.extract_from_bgr (image, rects)
                return [filename_path, landmarks]

            elif self.type == 'final':
                result = []
                faces = data[1]

                rel_output_name, abs_output_name, debug_file_name = ExtractSubprocessor.GenerateOutputPaths( filename_path, self.input_path, self.output_path )
                os.makedirs( os.path.dirname(abs_output_name), exist_ok=True)
                if self.debug:
                    os.makedirs( os.path.dirname(debug_file_name), exist_ok=True)
                    debug_image = image.copy()

                out_path,out_ext = os.path.splitext(abs_output_name)
                for (face_idx, face) in enumerate(faces):
                    if face is None: # indicates skip
                        output_file = "{}.skip".format( out_path )
                        open( output_file, 'w' )
                        break

                    output_file = "{}_{}{}".format( out_path, face_idx, out_ext )
                    rect = face[0]
                    image_landmarks = np.array(face[1])

                    if self.debug:
                        facelib.LandmarksProcessor.draw_rect_landmarks (debug_image, rect, image_landmarks, self.image_size, self.face_type)

                    if self.face_type == FaceType.MARK_ONLY:
                        face_image = image
                        face_image_landmarks = image_landmarks
                    else:
                        image_to_face_mat = facelib.LandmarksProcessor.get_transform_mat (image_landmarks, self.image_size, self.face_type)
                        face_image = cv2.warpAffine(image, image_to_face_mat, (self.image_size, self.image_size), cv2.INTER_LANCZOS4)
                        face_image_landmarks = facelib.LandmarksProcessor.transform_points (image_landmarks, image_to_face_mat)

                    cv2.imwrite(output_file, face_image)

                    a_png = AlignedPNG.load (output_file)

                    d = {
                      'face_type': FaceType.toString(self.face_type),
                      'landmarks': face_image_landmarks.tolist(),
                      'yaw_value': facelib.LandmarksProcessor.calc_face_yaw (face_image_landmarks),
                      'pitch_value': facelib.LandmarksProcessor.calc_face_pitch (face_image_landmarks),
                      'source_filename': rel_output_name,
                      'source_rect': rect,
                      'source_landmarks': image_landmarks.tolist()
                    }
                    a_png.setFaceswapDictData (d)
                    a_png.save(output_file)

                    result.append (output_file)

                if self.debug:
                    cv2.imwrite(debug_file_name, debug_image )

                return result
        return None

        #overridable
    def onClientGetDataName (self, data):
        #return string identificator of your data
        return data[0]

    #override
    def onHostResult (self, data, result):
        if self.manual == True:
            self.landmarks = result[1][0][1]

            image = self.annotated_img.copy()
            view_rect = (np.array(self.rect) * self.view_scale).astype(np.int).tolist()
            view_landmarks  = (np.array(self.landmarks) * self.view_scale).astype(np.int).tolist()
            facelib.LandmarksProcessor.draw_rect_landmarks (image, view_rect, view_landmarks, self.image_size, self.face_type)

            if self.param['rect_locked']:
                facelib.draw_landmarks(image, view_landmarks, (255,255,0) )
            elif self.param['skipped']:
                facelib.draw_landmarks(image, view_landmarks, (0,0,255) )
            self.param['redraw_needed'] = False

            cv2.imshow (self.wnd_name, image)
            return 0
        else:
            if self.type == 'rects':
                self.result.append ( result )
            elif self.type == 'landmarks':
                self.result.append ( result )
            elif self.type == 'final':
                self.result += result

            return 1

    #override
    def onHostProcessEnd(self):
        if self.manual == True:
            cv2.destroyAllWindows()

    #override
    def get_start_return(self):
        return self.result

    @staticmethod
    def GenerateOutputPaths( file_path, input_path, output_path ):
        file_name = os.path.basename(file_path)
        input_path = str(input_path)
        name, ext = os.path.splitext(file_name)
        input_path_trailing_slash = os.path.join(input_path,'')
        output_folder_trailing_slash = os.path.join( os.path.dirname( file_path ),'' )
        output_folders = output_folder_trailing_slash.replace( input_path_trailing_slash,'')

        relative_output_name = os.path.join( output_folders, file_name )
        abs_output_file_name = os.path.join( output_path, relative_output_name )
        debug_file_name = os.path.join( output_path, output_folders, "debug", "{}_debug{}".format(name, ext) )
        return relative_output_name, abs_output_file_name, debug_file_name

'''
detector
    'dlib'
    'mt'
    'manual'

face_type
    'full_face'
    'avatar'
'''
def main (input_dir, output_dir, debug, detector='mt', multi_gpu=True, manual_fix=False, manual_window_size=0, image_size=256, face_type='full_face', recursive=False, remove_existing=False):
    print ("Running extractor.\r\n")

    input_path = Path(input_dir)
    output_path = Path(output_dir)
    face_type = FaceType.fromString(face_type)

    if not input_path.exists():
        print('Input directory not found. Please ensure it exists.')
        return



#     if output_path.exists() and remove_existing:
#         for filename in Path_utils.get_image_paths(output_path, recursive=recursive):
#             Path(filename).unlink()
#     else:
#         output_path.mkdir(parents=True, exist_ok=True)
#
#     if debug:
#         debug_output_path = Path(str(output_path) + '_debug')
#         if debug_output_path.exists() and remove_existing:
#             for filename in Path_utils.get_image_paths(debug_output_path, recursive=recursive):
#                 Path(filename).unlink()
#         else:
#             debug_output_path.mkdir(parents=True, exist_ok=True)

    input_path_image_paths = Path_utils.get_image_unique_filestem_paths(input_path, verbose=True, recursive=recursive)
    if not remove_existing:
        for item in input_path_image_paths[:]:
            _, abs_output_name,_ = ExtractSubprocessor.GenerateOutputPaths( item, input_path, output_path )
            fname, fext = os.path.splitext(abs_output_name)
            face_names = []
            face_names.append( os.path.join( "{}.skip".format( fname )) )
            face_names.append( os.path.join(abs_output_name) )
            for face_idx in range(5):
                face_names.append( os.path.join( "{}_{}{}".format(fname, face_idx, fext)) )

            for face_file_name in face_names:
                if os.path.exists( face_file_name ):
                    input_path_image_paths.remove(item)
                    break

    images_found = len(input_path_image_paths)
    faces_detected = 0
    if images_found != 0:
        if detector == 'manual':
            print ('Performing manual extract...')
            extracted_faces = ExtractSubprocessor ([ (filename,[]) for filename in input_path_image_paths ], 'landmarks', image_size, face_type, debug, manual=True, manual_window_size=manual_window_size).process()
        else:
            print ('Performing 1st pass...')
            extracted_rects = ExtractSubprocessor ([ (x,) for x in input_path_image_paths ], 'rects', image_size, face_type, debug, multi_gpu=multi_gpu, manual=False, detector=detector).process()

            print ('Performing 2nd pass...')
            extracted_faces = ExtractSubprocessor (extracted_rects, 'landmarks', image_size, face_type, debug, multi_gpu=multi_gpu, manual=False).process()

            if manual_fix:
                print ('Performing manual fix...')

                if all ( np.array ( [ len(data[1]) > 0 for data in extracted_faces] ) == True ):
                    print ('All faces are detected, manual fix not needed.')
                else:
                    extracted_faces = ExtractSubprocessor (extracted_faces, 'landmarks', image_size, face_type, debug, manual=True, manual_window_size=manual_window_size).process()

        if len(extracted_faces) > 0:
            print ('Performing 3rd pass...')
            final_imgs_paths = ExtractSubprocessor (extracted_faces, 'final', image_size, face_type, debug, multi_gpu=multi_gpu, manual=False, output_path=output_path, input_path=input_path).process()
            faces_detected = len(final_imgs_paths)

    print('-------------------------')
    print('Images found:        %d' % (images_found) )
    print('Faces detected:      %d' % (faces_detected) )
    print('-------------------------')