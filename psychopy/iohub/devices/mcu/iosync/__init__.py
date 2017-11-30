# -*- coding: utf-8 -*-
# Part of the psychopy.iohub library.
# Copyright (C) 2012-2016 iSolver Software Solutions
# Distributed under the terms of the GNU General Public License (GPL).
from __future__ import division, absolute_import

"""
ioSync MCU Device.
===================

Uses a Teensy 3 or 3.1 and the ioSync.ino MCU sketch to create a general purpose
digital and analog I/O device connected to the ioHub PC via a USB cable.

See pysync.py file for details on how ioSync uses the Teensy 3 pins.

General capabilities:
~~~~~~~~~~~~~~~~~~~~~

Timing
-------

- 48 bit MCU usec timer, so MCU clock rolls every 8.9 years.
- Uses Cristian’s Algorithm (http://en.wikipedia.org/wiki/Cristian’s_algorithm)
  to convert MCU times to ioHub times, correcting for offset and drift between
  the time bases.


MCU Events
-----------

Two types of events can be generated by the ioSync MCU.

DigitalInputEvent
``````````````````

  Read 8 digital input lines. DIN state can be requested, or an change in DIN
  state can generate a MCU DigitalInputEvent which can be accessed using the
  iohub device getRxEvents() method, and is stored in the ioHub Data Store.


AnalogInputEvent
````````````````

  Read 8 channels of Analog Input, streamed at 1000Hz. Each read of the analog
  input lines is turned into a MCU AnalogInputEvent which can be accessed using
  the iohub device getRxEvents() method, and is stored in the ioHub Data Store.
  Effective resolution of analog inputs is TBD, but will likely be between
  11 - 13 bits.

MCU Control
------------

- Connect / Disconnect from the MCU
- Start / Stop recording of MCU events
- Set the state of 8 digital output lines.
    * setDigitalOutputByte() : set all eight DOUT lines using an 8 bit byte value
    * setDigitalOutputPin(): set the state of a single DOUT line.

MCU Access
-----------

- Request the current MCU time (requestTime() method).
- Get the current Digital Input state as an 8 bit byte value. ( getDigitalInputs() method )
- Get the current Analog Input values. ( getAnalogInputs() method )

"""

import numpy as np
import gevent

from ... import Device, DeviceEvent, Computer
from ....constants import DeviceConstants, EventConstants
from ....errors import print2err, printExceptionDetailsToStdErr
from .pysync import T3MC, T3Request, T3Event

getTime = Computer.getTime


class MCU(Device):
    """"""
    DEVICE_TIMEBASE_TO_SEC = 1.0
    _newDataTypes = [('serial_port', np.str, 32),
                     ('time_sync_interval', np.float32)]
    EVENT_CLASS_NAMES = [
        'AnalogInputEvent',
        'DigitalInputEvent',
        'ThresholdEvent']
    DEVICE_TYPE_ID = DeviceConstants.MCU
    DEVICE_TYPE_STRING = 'MCU'
    _mcu_slots = ['serial_port',
                  'time_sync_interval',
                  '_mcu',
                  '_request_dict',
                  '_response_dict',
                  '_last_sync_time'
                  ]
    __slots__ = [e for e in _mcu_slots]

    def __init__(self, *args, **kwargs):
        self.serial_port = None
        self.time_sync_interval = None

        Device.__init__(self, *args, **kwargs['dconfig'])

        self._mcu = None
        self._request_dict = {}
        self._response_dict = {}
        self._last_sync_time = 0.0

        if self.serial_port.lower() == 'auto':
            syncPorts = T3MC.findSyncs()
            if len(syncPorts) == 1:
                self.serial_port = syncPorts[0]
            elif len(syncPorts) > 1:
                self.serial_port = syncPorts[0]
            else:
                self.serial_port = None
        self.setConnectionState(self.serial_port is not None)

    def setConnectionState(self, enable):
        if enable is True:
            if self._mcu is None and self.serial_port:
                self._mcu = T3MC(self.serial_port)
                self._resetLocalState()

        elif enable is False:
            if self._mcu:
                self._mcu.resetState()
                self._mcu.close()
                self._mcu = None

        return self.isConnected()

    def getSerialPort(self):
        return self.serial_port

    def isConnected(self):
        return self._mcu is not None

    def getDeviceTime(self):
        return T3Request.sync_state.local2RemoteTime(getTime())

    def getSecTime(self):
        """Returns current device time in sec.msec format.

        Relies on a functioning getDeviceTime() method.

        """
        return self.getDeviceTime()  # *self.DEVICE_TIMEBASE_TO_SEC

    def enableEventReporting(self, enabled=True):
        """
        Specifies if the device should be reporting events to the ioHub Process
        (enabled=True) or whether the device should stop reporting events to the
        ioHub Process (enabled=False).


        Args:
            enabled (bool):  True (default) == Start to report device events to the ioHub Process. False == Stop Reporting Events to the ioHub Process. Most Device types automatically start sending events to the ioHUb Process, however some devices like the EyeTracker and AnlogInput device's do not. The setting to control this behavour is 'auto_report_events'

        Returns:
            bool: The current reporting state.
        """
        if enabled and not self.isReportingEvents():
            if not self.isConnected():
                self.setConnectionState(True)
            event_types = self.getConfiguration().get('monitor_event_types', [])
            enable_analog = 'AnalogInputEvent' in event_types
            enable_digital = 'DigitalInputEvent' in event_types
            enable_threshold = 'ThresholdEvent' in event_types
            self._enableInputEvents(
                enable_digital, enable_analog, enable_threshold)
        elif enabled is False and self.isReportingEvents() is True:
            if self.isConnected():
                self._enableInputEvents(False, False, False)

        return Device.enableEventReporting(self, enabled)

    def isReportingEvents(self):
        """Returns whether a Device is currently reporting events to the ioHub
        Process.

        Args: None

        Returns:
            (bool): Current reporting state.

        """
        return Device.isReportingEvents(self)

    # ioSync Request Type Wrappers below...
    #
    def requestTime(self):
        request = self._mcu.requestTime()
        self._request_dict[request.getID()] = request
        return request.asdict()

    def getDigitalInputs(self):
        request = self._mcu.getDigitalInputs()
        self._request_dict[request.getID()] = request
        return request.asdict()

    def getAnalogInputs(self):
        request = self._mcu.getAnalogInputs()
        self._request_dict[request.getID()] = request
        return request.asdict()

    def setDigitalOutputByte(self, new_dout_byte):
        request = self._mcu.setDigitalOutputByte(new_dout_byte)
        self._request_dict[request.getID()] = request
        return request.asdict()

    def generateKeyboardEvent(self, key_symbol, modifiers, press_duration):
        request = self._mcu.generateKeyboardEvent(key_symbol.lower(),
                                                  modifiers,
                                                  int(press_duration * 10))
        self._request_dict[request.getID()] = request
        return request.asdict()

    def setDigitalOutputPin(self, dout_pin_index, new_pin_state):
        request = self._mcu.setDigitalOutputPin(dout_pin_index, new_pin_state)
        self._request_dict[request.getID()] = request
        return request.asdict()

    def setAnalogThresholdValues(self, threshold_value_array):
        request = self._mcu.setAnalogThresholdValues(threshold_value_array)
        self._request_dict[request.getID()] = request
        return request.asdict()

    def _resetLocalState(self):
        self._request_dict.clear()
        self._response_dict.clear()
        self._last_sync_time = 0.0
        self._mcu._runTimeSync()
        self._last_sync_time = getTime()

    def resetState(self):
        request = self._mcu.resetState()
        self._resetLocalState()
        self._request_dict[request.getID()] = request
        return request.asdict()

    def getRequestResponse(self, rid=None):
        if rid:
            response = self._response_dict.get(rid)
            if response:
                del self._response_dict[rid]
                return response.asdict()
        else:
            resp_return = []
            responses = self._response_dict.values()
            self._response_dict.clear()
            for response in responses:
                resp_return.append(response.asdict())
            return resp_return

    def _enableInputEvents(
            self,
            enable_digital,
            enable_analog,
            threshold_events):
        self._mcu.enableInputEvents(
            enable_digital, enable_analog, threshold_events)

    def _poll(self):
        try:
            logged_time = getTime()

            if self.isConnected():
                self._mcu.getSerialRx()
                if logged_time - self._last_sync_time >= self.time_sync_interval:
                    self._mcu._runTimeSync()
                    self._last_sync_time = logged_time

            if not self.isReportingEvents():
                return False

            confidence_interval = logged_time - self._last_callback_time

            events = self._mcu.getRxEvents()
            for event in events:
                current_MCU_time = event.device_time  # self.getSecTime()
                device_time = event.device_time
                if event.local_time is None:
                    event.local_time = logged_time
                delay = logged_time - event.local_time  # current_MCU_time-device_time
                # local_time is in iohub time space already, so delay does not
                # need to be used to adjust iohub time
                iohub_time = event.local_time
                elist = None
                if event.getTypeInt() == T3Event.ANALOG_INPUT_EVENT:
                    elist = [EventConstants.UNDEFINED, ] * 19
                    elist[4] = AnalogInputEvent.EVENT_TYPE_ID
                    for i, v in enumerate(event.ain_channels):
                        elist[(i + 11)] = v
                elif event.getTypeInt() == T3Event.DIGITAL_INPUT_EVENT:
                    elist = [EventConstants.UNDEFINED, ] * 12
                    elist[4] = DigitalInputEvent.EVENT_TYPE_ID
                    elist[-1] = event.getDigitalInputByte()
                elif event.getTypeInt() == T3Event.THRESHOLD_EVENT:
                    elist = [EventConstants.UNDEFINED, ] * 19
                    elist[4] = ThresholdEvent.EVENT_TYPE_ID
                    for i, v in enumerate(event.threshold_state_changed):
                        elist[(i + 11)] = v

                if elist:
                    elist[0] = 0
                    elist[1] = 0
                    elist[2] = 0
                    elist[3] = Device._getNextEventID()
                    elist[5] = device_time
                    elist[6] = logged_time
                    elist[7] = iohub_time
                    elist[8] = confidence_interval
                    elist[9] = delay
                    elist[10] = 0

                    self._addNativeEventToBuffer(elist)

            replies = self._mcu.getRequestReplies(True)
            for reply in replies:
                rid = reply.getID()
                if rid in self._request_dict.keys():
                    self._response_dict[rid] = reply
                    del self._request_dict[rid]

            self._last_callback_time = logged_time
            return True
        except Exception as e:
            print2err('--------------------------------')
            print2err('ERROR in MCU._poll: ', e)
            printExceptionDetailsToStdErr()
            print2err('---------------------')

    def _close(self):
        if self._mcu:
            self.resetState()
            self.setConnectionState(False)

        Device._close(self)


class AnalogInputEvent(DeviceEvent):
    _newDataTypes = [
        ('AI_0', np.float32),
        ('AI_1', np.float32),
        ('AI_2', np.float32),
        ('AI_3', np.float32),
        ('AI_4', np.float32),
        ('AI_5', np.float32),
        ('AI_6', np.float32),
        ('AI_7', np.float32)
    ]
    EVENT_TYPE_ID = EventConstants.ANALOG_INPUT
    EVENT_TYPE_STRING = 'ANALOG_INPUT'
    IOHUB_DATA_TABLE = EVENT_TYPE_STRING
    PARENT_DEVICE = MCU
    __slots__ = [e[0] for e in _newDataTypes]

    def __init__(self, *args, **kwargs):
        DeviceEvent.__init__(self, *args, **kwargs)


class ThresholdEvent(DeviceEvent):
    _newDataTypes = [
        ('AI_0', np.float32),
        ('AI_1', np.float32),
        ('AI_2', np.float32),
        ('AI_3', np.float32),
        ('AI_4', np.float32),
        ('AI_5', np.float32),
        ('AI_6', np.float32),
        ('AI_7', np.float32)
    ]
    EVENT_TYPE_ID = EventConstants.THRESHOLD
    EVENT_TYPE_STRING = 'THRESHOLD'
    IOHUB_DATA_TABLE = EVENT_TYPE_STRING
    PARENT_DEVICE = MCU
    __slots__ = [e[0] for e in _newDataTypes]

    def __init__(self, *args, **kwargs):
        DeviceEvent.__init__(self, *args, **kwargs)


class DigitalInputEvent(DeviceEvent):
    _newDataTypes = [('state', np.uint8)  # the value of the 8 digital input lines as an uint8.
                     ]
    EVENT_TYPE_ID = EventConstants.DIGITAL_INPUT
    EVENT_TYPE_STRING = 'DIGITAL_INPUT'
    IOHUB_DATA_TABLE = EVENT_TYPE_STRING
    PARENT_DEVICE = MCU
    __slots__ = [e[0] for e in _newDataTypes]

    def __init__(self, *args, **kwargs):
        self.state = 0
        DeviceEvent.__init__(self, *args, **kwargs)
