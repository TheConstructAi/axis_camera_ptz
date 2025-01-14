#!/usr/bin/env python

# Software License Agreement (BSD License)
#
# Copyright (c) 2014, Robotnik Automation SLL
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Robotnik Automation SSL nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

import sys
import time
import threading
import base64
import datetime
import numpy as np
import socket
import math

import rospkg, rospy
import os


from std_msgs.msg import String, Bool, Float64
from std_srvs.srv import Empty
from sensor_msgs.msg import CompressedImage, CameraInfo
import camera_info_manager
from sensor_msgs.msg import JointState

from robotnik_msgs.msg import Axis as AxisMsg
from robotnik_msgs.msg import ptz
from axis_camera_ptz.srv import set_ptz
import diagnostic_updater
import diagnostic_msgs



class AxisPTZ(threading.Thread):
    """
        Class interface to set the Pan Tilt Zoom of the camera
    """
    def __init__(self, args):
        self.rate = args['ptz_rate']

        self.autoflip = args['autoflip']
        self.eflip = args['eflip']
        self.eflip = args['eflip']
        self.tilt_joint = args['tilt_joint']
        self.pan_joint = args['pan_joint']
        self.tilt_joint_command = args['tilt_joint_command']
        self.pan_joint_command = args['pan_joint_command']
        self.min_pan_value = args['min_pan_value']
        self.max_pan_value = args['max_pan_value']
        self.min_tilt_value = args['min_tilt_value']
        self.max_tilt_value = args['max_tilt_value']
        self.min_zoom_value = args['min_zoom_value']
        self.max_zoom_value = args['max_zoom_value']
        self.home_pan_value = args['home_pan_value']
        self.home_tilt_value = args['home_tilt_value']
        self.error_pos = args['error_pos']
        self.error_zoom = args['error_zoom']
        self.joint_states_topic = args['joint_states_topic']
        self.use_control_timeout = args['use_control_timeout']
        self.control_timeout_value = args['control_timeout_value']
        self.invert_ptz = args['invert_ptz']
        self.initialization_delay = args['initialization_delay']

        self.current_ptz = AxisMsg()
        self.last_msg = ptz()
        threading.Thread.__init__(self)

        self.daemon = True
        self.run_control = True
        # Flag to know if the current params of the camera has been read
        self.ptz_syncronized = False
        # used in control position (degrees)

        self.desired_pan = 0.0
        self.desired_tilt = 0.0
        self.desired_zoom = 0.0
        self.error_reading = False
        self.error_reading_msg = ''

        # Timer to get/release ptz control
        if(self.use_control_timeout):
            self.last_command_time = rospy.Time(0)
            self.command_timeout = rospy.Duration(self.control_timeout_value)

    def rosSetup(self):
        """
            Sets the ros connections
        """
        self.pub = rospy.Publisher("~camera_params", AxisMsg, queue_size=10)
        self.pub_command_pan = rospy.Publisher(self.pan_joint_command, Float64, queue_size=10)
        self.pub_command_tilt = rospy.Publisher(self.tilt_joint_command, Float64, queue_size=10)

        #self.sub = rospy.Subscriber("cmd", Axis, self.cmd)
        self.sub = rospy.Subscriber("~ptz_command", ptz, self.commandPTZCb)
        self.sub_joint_states = rospy.Subscriber("joint_states", JointState, self.jointStateCb)

        # Services
        self.home_service = rospy.Service('~home_ptz', Empty, self.homeService)


    def commandPTZCb(self, msg):
        """
            Command for ptz movements
        """
        self.setCommandPTZ(msg)




    def setCommandPTZ(self, command):

        # Save time of requested command
        if(self.use_control_timeout):
            self.last_command_time = rospy.get_rostime()
            #rospy.loginfo("Last command time %i %i", self.last_command_time.secs, self.last_command_time.nsecs)
        if self.invert_ptz:
            invert_command = -1.0
        else:
            invert_command = 1.0
        # Need to convert from rad to degree
        # relative motion
        if command.relative:
            new_pan = invert_command*command.pan + self.desired_pan
            new_tilt = invert_command*command.tilt + self.desired_tilt
            new_zoom = self.desired_zoom + command.zoom

        else:
            # new_pan = math.degrees(invert_command*command.pan)
            # new_tilt = math.degrees(invert_command*command.tilt)
            # The input values are daians and the comands sent through topics are radians also
            # So no need for degrees conversion
            new_pan = invert_command*command.pan
            new_tilt = invert_command*command.tilt
            new_zoom = command.zoom



        # Applies limit restrictions
        if new_pan > self.max_pan_value:
            new_pan = self.max_pan_value
        elif new_pan < self.min_pan_value:
            new_pan = self.min_pan_value
        if new_tilt > self.max_tilt_value:
            new_tilt = self.max_tilt_value
        elif new_tilt < self.min_tilt_value:
            new_tilt = self.min_tilt_value
        if new_zoom > self.max_zoom_value:
            new_zoom = self.max_zoom_value
        elif new_zoom < self.min_zoom_value:
            new_zoom = self.min_zoom_value

        self.desired_pan = new_pan
        self.desired_tilt = new_tilt
        self.desired_zoom = new_zoom



    def homeService(self, req):

        # Set home values
        home_command = ptz()
        home_command.relative = False
        home_command.pan = self.home_pan_value
        home_command.tilt = self.home_tilt_value
        home_command.zoom = 0

        self.setCommandPTZ(home_command)

        return {}


    def controlPTZ(self):
        """
            Performs the control of the camera ptz
        """
        # Only if it's syncronized
        if self.ptz_syncronized:
            rospy.logdebug("Sending Command==>PAN="+str(self.desired_pan)+",TILT="+str(self.desired_tilt)+",ZOOM="+str(self.desired_zoom))
            #if self.isPTZinPosition():
            self.sendPTZCommand()
            #else:
            #rospy.logwarn('controlPTZ: not in  position')


    def isPTZinPosition(self):
        """
            @return True if camera has the desired position / settings
        """
        if abs(self.current_ptz.pan - self.desired_pan) <= self.error_pos and abs(self.current_ptz.tilt - self.desired_tilt) <= self.error_pos and abs(self.current_ptz.zoom - self.desired_zoom) <= self.error_zoom:
            rospy.logwarn('isPTZinPosition: pan %.3lf vs %.3lf', self.current_ptz.pan, self.desired_pan)
            rospy.logwarn('isPTZinPosition: tilt %.3lf vs %.3lf', self.current_ptz.tilt, self.desired_tilt)
            rospy.logwarn('isPTZinPosition: zoom %.3lf vs %.3lf', self.current_ptz.zoom, self.desired_zoom)
            return True
        else:
            return False

    def sendPTZCommand(self):
        """
            Sends the ptz to the camera
        """
        msg = Float64()
        msg.data = self.desired_pan
        self.pub_command_pan.publish(msg)
        msg.data = self.desired_tilt
        self.pub_command_tilt.publish(msg)

    def jointStateCb(self, msg):
        try:
            pan_index = msg.name.index(self.pan_joint)
            self.current_ptz.pan = msg.position[pan_index]
            tilt_index = msg.name.index(self.tilt_joint)
            self.current_ptz.tilt = msg.position[tilt_index]
        except:
            rospy.logwarn_throttle(10, "Cannot find " + self.pan_joint + " in joint_state message")


    def getPTZState(self):
        """
            Gets the current ptz state/position of the camera
        """

        # First time saves the current values
        if not self.ptz_syncronized:
            self.desired_pan = self.current_ptz.pan
            self.desired_tilt = self.current_ptz.tilt
            self.desired_zoom = self.current_ptz.zoom
            #rospy.loginfo('%s:getPTZState: PTZ state syncronized!', rospy.get_name())
            self.ptz_syncronized = True


    def run(self):
        """
            Executes the thread
        """
        r = rospy.Rate(self.rate)


        while not rospy.is_shutdown():

            self.getPTZState()

            if(self.use_control_timeout):
                self.manageControl()

            # Performs interaction with the camera if it is enabled
            if self.run_control:
                self.controlPTZ()
            # Publish ROS msgs
            self.publishROS()


            r.sleep()

        print("Bye!")


    def publishROS(self):
        """
            Publish to ROS server
        """
        # Publishes the current PTZ values
        self.pub.publish(self.current_ptz)


    def get_data(self):
        return self.msg

    def stop_control(self):
        """
            Stops the control loop
        """
        self.run_control = False

    def start_control(self):
        """
            Starts the control loop
        """
        self.run_control = True

    def manageControl(self):
        """
            Gets/releases ptz control using a timeout
        """

        if(rospy.get_rostime() - self.last_command_time < self.command_timeout):
            if not self.run_control:
                self.start_control()
        else:
            if self.run_control:
                self.stop_control()


    def peer_subscribe(self, topic_name, topic_publish, peer_publish):
        """
            Callback when a peer has subscribed from a topic
        """

        if not self.run_control:
            self.start_control()

    def peer_unsubscribe(self, topic_name, num_peers):
        """
            Callback when a peer has unsubscribed from a topic
        """
        #print 'Num of peers = %d'%num_peers

        if num_peers == 0:
            self.stop_control()

    def getStateDiagnostic(self, stat):
        """
        Callback to analyze the state of ptz the params read from the camera
        """

        if self.error_reading:
            stat.summary(diagnostic_msgs.msg.DiagnosticStatus.ERROR, "Error getting ptz data: %s" % self.error_reading_msg)
        else:
            stat.summary(diagnostic_msgs.msg.DiagnosticStatus.OK, "Reading ptz data")

        stat.add("rate", self.rate)
        stat.add("pan", self.current_ptz.pan)
        stat.add("tilt", self.current_ptz.tilt)
        stat.add("zoom", self.current_ptz.zoom)

        return stat


class Axis():
    """
        Class Axis. Intended to read video from the IP camera and publish to ROS
    """

    def __init__(self, args):
        """
            Init method.
            Initializes attributes and read passed args
        """
        self.enable_auth = args['enable_auth']
        self.hostname = args['hostname']
        self.username = 'root'
        self.password = args['password']
        self.camera_id = args['camera_id']
        self.camera_number = args['camera_number']
        self.camera_model = args['camera_model']
        self.fps = args['fps']
        self.compression = args['compression']
        self.axis_frame_id = args['frame']
        self.ptz = args['ptz'] # Flag to add the ptz control
        self.profile = args['profile']
        self.initialization_delay = args['initialization_delay']

        # by default is stopped
        self.run_camera = False

        self.videocodec = 'mpeg4' # h264, mpeg4


        self.timeout = 5 # seconds
        self.last_update = datetime.datetime.now()  # To control the data reception

        self.status = 'ERROR'

        # time between reconnections
        self.reconnection_time = 5
        # timeout when calling urlopen
        self.timeout = 5

        self.subscribers = 0
        # Object to set the Pan Tilt and Zoom
        self.ptz_interface = AxisPTZ(args)

        self.error_reading = False
        self.error_reading_msg = ''



    def rosSetup(self):
        """
            Creates and setups ROS components
        """
        # Diagnostic Updater
        self.diagnostics_updater = diagnostic_updater.Updater()
        self.diagnostics_updater.setHardwareID("%s-%s:%s" % (self.camera_model, self.camera_id, self.hostname) )
        self.diagnostics_updater.add("Video stream updater", self.getStreamDiagnostic)
        #self.diagnostics_updater.add("Video stream frequency", self.getStreamFrequencyDiagnostic)

        if self.fps == 0:
            freq_bounds = {'min':0.5, 'max':100}
        else:
            freq_bounds = {'min':self.fps, 'max':self.fps}

        self.image_pub_freq = diagnostic_updater.HeaderlessTopicDiagnostic("%scompressed"%rospy.get_namespace(), self.diagnostics_updater,
            diagnostic_updater.FrequencyStatusParam(freq_bounds, 0.01, 1))

        if self.ptz:
            # sets the ptz interface
            self.ptz_interface.rosSetup()
            self.diagnostics_updater.add("Ptz state updater", self.ptz_interface.getStateDiagnostic)

        # Creates a periodic callback to publish the diagnostics at desired freq
        self.diagnostics_timer = rospy.Timer(rospy.Duration(1.0), self.publishDiagnostics)


    def run(self):
        """
            Executes the main loop of the node
        """
        rospy.logwarn('%s:run: waiting %.3lf secs before running', rospy.get_name(), self.initialization_delay)
        time.sleep(self.initialization_delay)


        if self.ptz:
            # Starts Thread for PTZ
            self.ptz_interface.start()

        while not rospy.is_shutdown():

            try:
                if self.run_camera:
                    self.stream()

                #rospy.loginfo('stream')
            except:
                import traceback
                traceback.print_exc()

            #rospy.loginfo('Axis:run: Camera %s (%s:%d) reconnecting in %d seconds'%(self.camera_id, self.hostname, self.camera_number, self.reconnection_time))
            if not rospy.is_shutdown():
                time.sleep(self.reconnection_time)



    def peer_subscribe(self, topic_name, topic_publish, peer_publish):
        """
            Callback when a peer has subscribed from a topic
        """
        self.subscribers = self.subscribers + 1

        if not self.run_camera:
            rospy.loginfo('Axis:peer_subscribe: %s. Start reading from camera'%(rospy.get_name()))
            self.run_camera = True


    def peer_unsubscribe(self, topic_name, num_peers):
        """
            Callback when a peer has unsubscribed from a topic
        """
        self.subscribers = self.subscribers - 1

        if self.subscribers == 0 and self.run_camera:
            rospy.loginfo('Axis:peer_unsubscribe: %s. Stop reading from camera'%(rospy.get_name()))
            self.run_camera = False

    def stream(self):
        pass

    def publishDiagnostics(self,event):
        """
            Publishes the diagnostics at the desired rate
        """
        # Updates diagnostics
        self.diagnostics_updater.update()


    def rosShutdown(self):
        """
            Performs the shutdown of the ROS interfaces
        """
        pass


    def getStreamDiagnostic(self, stat):
        """
            Callback to analyze the reception of the video stream
        """
        if self.error_reading:
            stat.summary(diagnostic_msgs.msg.DiagnosticStatus.ERROR, "Error receiving video stream: %s" % self.error_reading_msg)
        else:
            stat.summary(diagnostic_msgs.msg.DiagnosticStatus.OK, "Getting video stream")
        stat.add("camera id", self.camera_id)
        stat.add("camera number", self.camera_number)
        stat.add("camera model", self.camera_model)
        stat.add("fps", str(self.fps))
        stat.add("compression", str(self.compression))
        stat.add("ptz", self.ptz)
        stat.add("video_frame", self.axis_frame_id)
        stat.add("running", self.run_camera)

        return stat


def main():

    rospy.init_node("axis_camera", log_level=rospy.WARN)

    axis_node_name = rospy.get_name()
    axis_node_namespace = rospy.get_namespace()

    print("namespace = %s, name = %s"%(axis_node_namespace, axis_node_name))

    # default params
    arg_defaults = {
      'hostname': '192.168.1.205',
      'password': 'R0b0tn1K',
      'username': 'root',
      'enable_auth': True,
      'camera_number': 1, # camera number
      'camera_id': 'XXXX', # internal id (if necessary)
      'camera_model': 'axis_p5512',
      'profile': 'Test',
      'fps': 0, # max
      'compression': 0, # 0->100
      'frame': 'axis_camera1',
      'ptz': False,
      'autoflip': False,
      'eflip': False,
      'pan_joint': 'pan',
      'tilt_joint': 'tilt',
      'pan_joint_command': '/rbcar/front_ptz_camera_pan_joint_position_controller/command',
      'tilt_joint_command': '/rbcar/front_ptz_camera_tilt_joint_position_controller/command',
      'min_pan_value': -2.97,
      'max_pan_value': 2.97,
      'min_tilt_value': 0,
      'max_tilt_value': 1.57,
      'max_zoom_value': 20000,
      'min_zoom_value': 0,
      'home_pan_value': 0.0,
      'home_tilt_value': 0.79,
      'ptz_rate': 5.0,
      'error_pos': 0.02,
      'error_zoom': 99.0,
      'joint_states_topic': 'joint_states',
      'use_control_timeout': False,
      'control_timeout_value': 5.0,
      'invert_ptz': False,
      'initialization_delay': 0.0 # time waiting before running
    }
    args = {}


    for name in arg_defaults:

        param_name = '%s%s'%(axis_node_namespace,name)

        if rospy.search_param(param_name):
            args[name] = rospy.get_param(param_name)
        else:
            args[name] = arg_defaults[name]

    rospy.loginfo('%s: args: %s'%(axis_node_name, args))

    axis = Axis(args)
    axis.rosSetup()
    rospy.loginfo('%s: starting'%axis_node_name)

    axis.run()

if __name__ == "__main__":
    main()

