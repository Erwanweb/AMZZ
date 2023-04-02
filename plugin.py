"""
Smart Virtual Thermostat FOR zone in central duct air system python plugin for Domoticz
Author: Erwanweb,
        adapted from a lot of things
Version:    0.0.1: alpha
            0.0.2: beta
"""
"""
<plugin key="AMZZ" name="AC Zone control in multizone central duct air system" author="Erwanweb" version="0.0.2" externallink="https://github.com/Erwanweb/AMZZ.git">
    <description>
        <h2>ZONE control in multizone central duct air system</h2><br/>
        V.0.0.2<br/>
        Easily implement in Domoticz a zone control in multizone central duct air system<br/>
        <h3>Set-up and Configuration</h3>
    </description>
    <params>
        <param field="Address" label="Domoticz IP Address" width="200px" required="true" default="127.0.0.1"/>
        <param field="Port" label="Port" width="40px" required="true" default="8080"/>
        <param field="Username" label="Username" width="200px" required="false" default=""/>
        <param field="Password" label="Password" width="200px" required="false" default=""/>
        <param field="Mode1" label="Inside Temp Sensors (csv list of idx)" width="100px" required="true" default="0"/>
        <param field="Mode2" label="Main duct heating mode (csv list of idx)" width="100px" required="false" default=""/>
        <param field="Mode3" label="Air Valves (csv list of idx)" width="100px" required="true" default="0"/>
        <param field="Mode5" label="hysterisis, Delta Max(in tenth of degre)" width="200px" required="true" default="10,20"/>
        <param field="Mode6" label="Logging Level" width="200px">
            <options>
                <option label="Normal" value="Normal"  default="true"/>
                <option label="Verbose" value="Verbose"/>
                <option label="Debug - Python Only" value="2"/>
                <option label="Debug - Basic" value="62"/>
                <option label="Debug - Basic+Messages" value="126"/>
                <option label="Debug - Connections Only" value="16"/>
                <option label="Debug - Connections+Queue" value="144"/>
                <option label="Debug - All" value="-1"/>
            </options>
        </param>
    </params>
</plugin>
"""
import Domoticz
import json
import urllib.parse as parse
import urllib.request as request
from datetime import datetime, timedelta
import time
import base64
import itertools

class deviceparam:

    def __init__(self, unit, nvalue, svalue):
        self.unit = unit
        self.nvalue = nvalue
        self.svalue = svalue


class BasePlugin:

    def __init__(self):

        self.debug = False
        self.setpoint = 21.0
        self.hyste = 5  # allowed deltamax from setpoint for high level airfan
        self.deltamax = 20  # allowed deltamax from setpoint for high level airfan
        self.ActiveSensors = {}
        self.InTempSensors = []
        self.Mainductmode = []
        self.powerrequest = False
        self.Airvalve = []
        self.intemp = 20.0
        self.intemperror = False
        self.nexttemps = datetime.now()
        self.Mainductheatmode = False
        self.learn = True
        return


    def onStart(self):

        # setup the appropriate logging level
        try:
            debuglevel = int(Parameters["Mode6"])
        except ValueError:
            debuglevel = 0
            self.loglevel = Parameters["Mode6"]
        if debuglevel != 0:
            self.debug = True
            Domoticz.Debugging(debuglevel)
            DumpConfigToLog()
            self.loglevel = "Verbose"
        else:
            self.debug = False
            Domoticz.Debugging(0)

        # create the child devices if these do not exist yet
        devicecreated = []
        if 1 not in Devices:
            Options = {"LevelActions": "||",
                       "LevelNames": "Off|Manual|Auto",
                       "LevelOffHidden": "false",
                       "SelectorStyle": "0"}
            Domoticz.Device(Name="AC zone Control", Unit=1, TypeName="Selector Switch", Switchtype=18, Image=15,
                            Options=Options, Used=1).Create()
            devicecreated.append(deviceparam(1, 0, "0"))  # default is Off state
        if 2 not in Devices:
            Options = {"LevelActions": "||",
                       "LevelNames": "Off|Cool|Heat",
                       "LevelOffHidden": "true",
                       "SelectorStyle": "0"}
            Domoticz.Device(Name="AC Zone Manual Mode", Unit=2, TypeName="Selector Switch", Switchtype=18, Image=15,
                            Options=Options, Used=1).Create()
            devicecreated.append(deviceparam(2, 0, "10"))  # default is normal confort mode
        if 3 not in Devices:
            Domoticz.Device(Name="AC Zone heating mode", Unit=3, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(3, 0, ""))  # default is Off
        if 4 not in Devices:
            Domoticz.Device(Name="AC Zone Heating prioritye", Unit=4, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(4, 0, ""))  # default is Off
        if 5 not in Devices:
            Domoticz.Device(Name="AC Zone Setpoint", Unit=5, Type=242, Subtype=1, Used=1).Create() # default is 21
            devicecreated.append(deviceparam(5,0,"21"))  # default is 21 degrees
        if 6 not in Devices:
            Domoticz.Device(Name="AC Zone Room temp", Unit=6, TypeName="Temperature", Used=1).Create()
            devicecreated.append(deviceparam(6, 0, "20"))  # default is 20 degrees
        if 7 not in Devices:
            Domoticz.Device(Name="AC Zone power Request", Unit=7, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(7, 0, ""))  # default is Off
        if 8 not in Devices:
            Domoticz.Device(Name="AC Zone turbo Request", Unit=8, TypeName="Switch", Image=9, Used=1).Create()
            devicecreated.append(deviceparam(8, 0, ""))  # default is Off

        # if any device has been created in onStart(), now is time to update its defaults
        for device in devicecreated:
            Devices[device.unit].Update(nValue=device.nvalue, sValue=device.svalue)

        # build lists of sensors and switches
        self.InTempSensors = parseCSV(Parameters["Mode1"])
        Domoticz.Debug("Inside Temperature sensors = {}".format(self.InTempSensors))
        self.Mainductmode = parseCSV(Parameters["Mode2"])
        Domoticz.Debug("Main duct heat mode = {}".format(self.Mainductmode))
        self.Airvalve = parseCSV(Parameters["Mode3"])
        Domoticz.Debug("Air Valve = {}".format(self.Airvalve))

        # build dict of status of all temp sensors to be used when handling timeouts
        for sensor in itertools.chain(self.InTempSensors):
            self.ActiveSensors[sensor] = True

        # splits additional parameters
        params = parseCSV(Parameters["Mode5"])
        if len(params) == 2:
            self.hyste = CheckParam("Hysterisis", params[0], 5)
            self.deltamax = CheckParam("Delta Max", params[1], 20)
        else:
            Domoticz.Error("Error reading Mode5 parameters")


        # Check if the used control mode is ok
        if (Devices[1].sValue == "0"):
            self.powerOn = 0
        else :
            self.powerOn = 1


    def onStop(self):

        Domoticz.Debugging(0)


    def onCommand(self, Unit, Command, Level, Color):

        Domoticz.Debug("onCommand called for Unit {}: Command '{}', Level: {}".format(Unit, Command, Level))

        if Unit == 1:  # zone control
            self.powerOn = 1 if Level > 0 else 0
            Devices[1].Update(nValue = self.powerOn,sValue = str(Level))
            Devices[2].Update(nValue = self.powerOn,sValue = Devices[2].sValue)
            self.onHeartbeat()

        if Unit == 2:  # zone mode
            Devices[2].Update(nValue = self.powerOn,sValue = str(Level))
            self.onHeartbeat()

        if Unit == 5:  # setpoint
            Devices[5].Update(nValue = 0,sValue = str(Level))
            self.onHeartbeat()

        if Unit in (3, 4, 6, 7): # tout le reste
            self.onHeartbeat()


    def onHeartbeat(self):

        now = datetime.now()

        # fool proof checking.... based on users feedback
        if not all(device in Devices for device in (1,2,3,4,5,6,7)):
            Domoticz.Error("one or more devices required by the plugin is/are missing, please check domoticz device creation settings and restart !")
            return

        self.Mainductmodecontrol()  # check mode of the main duct

        if self.powerOn:
            if Devices[4].nValue == 1:  # Main duct is in heat mode - heating priority
                if Devices[2].sValue == "10":  # Mode is COOLING
                    Devices[2].Update(nValue = self.powerOn,sValue = "20")  # Mode is HEATING
        else :
            if Devices[2].sValue == "20":  # Mode is HEATING
                Devices[2].Update(nValue = self.powerOn,sValue = "10")  # Mode is COOLING

        if Devices[2].sValue == "20":  # Zone is in heat mode
            self.setpoint = (float(Devices[5].sValue) + ((self.hyste) / 10))
        else :
            self.setpoint = (float(Devices[5].sValue) - ((self.hyste) / 10))


        if Devices[1].sValue == "0":  # Thermostat is off
            Domoticz.Log("Thermostat is OFF")
            self.powerrequest = False
            Domoticz.Debug("Switching zone Off !")
            if not Devices[3].nValue == 0:
                Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)
            if not Devices[7].nValue == 0:
                Devices[7].Update(nValue = 0,sValue = Devices[7].sValue)
            if Devices[8].nValue == 1:
                Devices[8].Update(nValue = 0,sValue = Devices[8].sValue)

        elif Devices[1].sValue == "10":  # Thermostat is in manual mode
            Domoticz.Debug("Thermostat is in manual mode")
            if Devices[2].sValue == "10":  # Mode is COOLING
                if not Devices[3].nValue == 0:
                    Devices[3].Update(nValue = 0,sValue = Devices[3].sValue)
                if self.intemp > self.setpoint : # we have a cooling request
                    Domoticz.Debug("Manual cooling mode : we have cooling request")
                    Domoticz.Debug("Romm temp is : " + str(self.intemp))
                    Domoticz.Debug("setpoint is : " + str(self.setpoint))
                    self.powerrequest = True
                    if Devices[7].nValue == 0:
                       Devices[7].Update(nValue = 1,sValue = Devices[7].sValue)
                    if self.intemp > (self.setpoint + (self.deltamax / 10)):  # we have a turbo request
                        Domoticz.Debug("Manual cooling mode : we have turbo request")
                        if Devices[8].nValue == 0:
                            Devices[8].Update(nValue = 1,sValue = Devices[8].sValue)
                    else :
                        if Devices[8].nValue == 1:
                            Devices[8].Update(nValue = 0,sValue = Devices[8].sValue)
                else:
                    Domoticz.Debug("Manual cooling mode : setpoint is reached")
                    self.powerrequest = False
                    if Devices[7].nValue == 1:
                       Devices[7].Update(nValue = 0,sValue = Devices[7].sValue)
                    if Devices[8].nValue == 1:
                       Devices[8].Update(nValue = 0,sValue = Devices[8].sValue)

            elif Devices[2].sValue == "20":  # Mode is HEATING
                if not Devices[3].nValue == 1:
                    Devices[3].Update(nValue = 1,sValue = Devices[3].sValue)
                if self.intemp < self.setpoint : # we have a heating request
                    Domoticz.Debug("Manual heating mode : we have heating request")
                    Domoticz.Debug("Romm temp is : " + str(self.intemp))
                    Domoticz.Debug("setpoint is : " + str(self.setpoint))
                    self.powerrequest = True
                    if Devices[7].nValue == 0:
                        Devices[7].Update(nValue = 1,sValue = Devices[7].sValue)
                    if self.intemp < (self.setpoint - (self.deltamax / 10)):  # we have a turbo request
                        Domoticz.Debug("Manual heating mode : we have turbo request")
                        if Devices[8].nValue == 0:
                            Devices[8].Update(nValue = 1,sValue = Devices[8].sValue)
                    else :
                        if Devices[8].nValue == 1:
                            Devices[8].Update(nValue = 0,sValue = Devices[8].sValue)

                else:
                    Domoticz.Debug("Manual heating mode : setpoint is reached")
                    self.powerrequest = False
                    if Devices[7].nValue == 1:
                        Devices[7].Update(nValue = 0,sValue = Devices[7].sValue)
                    if Devices[8].nValue == 1:
                       Devices[8].Update(nValue = 0,sValue = Devices[8].sValue)

        else:  # Thermostat is in auto mode
            Domoticz.Debug("Thermostat is in auto mode")
            if not Devices[3].nValue == 1:
                Devices[3].Update(nValue = 1,sValue = Devices[3].sValue)
                Devices[2].Update(nValue = 1,sValue = "20") # Mode is HEATING
            if self.intemp < self.setpoint :# we have a heating request
                Domoticz.Debug("AUTO : we have heating request")
                Domoticz.Debug("Romm temp is : " + str(self.intemp))
                Domoticz.Debug("setpoint is : " + str(self.setpoint))
                self.powerrequest = True
                if Devices[7].nValue == 0:
                   Devices[7].Update(nValue = 1,sValue = Devices[7].sValue)
                if self.intemp < (self.setpoint - (self.deltamax / 10)):  # we have a turbo request
                    Domoticz.Debug("AUTO heating mode : we have turbo request")
                    if Devices[8].nValue == 0:
                        Devices[8].Update(nValue=1, sValue=Devices[8].sValue)
                else:
                    if Devices[8].nValue == 1:
                        Devices[8].Update(nValue=0, sValue=Devices[8].sValue)
            else :
                Domoticz.Debug("AUTO : setpoint is reached")
                self.powerrequest = False
                if Devices[7].nValue == 1:
                   Devices[7].Update(nValue = 0,sValue = Devices[7].sValue)
                if Devices[8].nValue == 1:
                    Devices[8].Update(nValue = 0,sValue = Devices[8].sValue)


        # flip on / off as needed
        # self.powerrequest = switch
        command = "On" if self.powerrequest else "Off"
        Domoticz.Debug("Air valve '{}'".format(command))
        for idx in self.Airvalve:
            DomoticzAPI("type=command&param=switchlight&idx={}&switchcmd={}".format(idx,command))

        # temperature devices update
        if self.nexttemps <= now:
            # call the Domoticz json API for a temperature devices update, to get the lastest temps (and avoid the
            # connection time out time after 10mins that floods domoticz logs in versions of domoticz since spring 2018)
            self.readTemps()

    def Mainductmodecontrol(self):

        Domoticz.Debug("Checking Main duct modee...")
        self.Mainductheatmode = False
        # Build list of Heating requester device, with their current status
        Mainductmodeswitch = {}
        devicesAPI = DomoticzAPI("type=devices&filter=light&used=true&order=Name")
        if devicesAPI:
            for device in devicesAPI["result"]:  # parse the Heating requester device
                idx = int(device["idx"])
                if idx in self.Mainductmode:  # this is one of our Heating requester switch
                    if "Status" in device:
                        Mainductmodeswitch[idx] = True if device["Status"] == "On" else False
                        Domoticz.Debug(
                            "Main duct heating mode switch {} currently is '{}'".format(idx,device["Status"]))
                        if device["Status"] == "On":
                            self.Mainductheatmode = True

                    else:
                        Domoticz.Error(
                            "Device with idx={} does not seem to be a Main duct heating mode switch !".format(idx))

        # fool proof checking....
        if len(Mainductmodeswitch) == 0:
            Domoticz.Error(
                "none of the devices in the 'Main duct heating mode switch' parameter is a switch... no action !")
            self.Mainductheatmode = False
            self.powerrequest = False
            Devices[4].Update(nValue = 0,sValue = Devices[4].sValue)
            return

        if self.Mainductheatmode:
            Domoticz.Debug("Main duct is in heating priority mode cause of at mini 1 zone is in heating mode...")
            if Devices[4].nValue == 0:
                Devices[4].Update(nValue = 1,sValue = Devices[4].sValue)

        else:
            Domoticz.Debug("Main duct is in cooling mode...")
            if Devices[4].nValue == 1:
                Devices[4].Update(nValue = 0,sValue = Devices[4].sValue)


    def readTemps(self):

        # set update flag for next temp update
        self.nexttemps = datetime.now() + timedelta(minutes=2)

        # fetch all the devices from the API and scan for sensors
        noerror = True
        listintemps = []
        devicesAPI = DomoticzAPI("type=devices&filter=temp&used=true&order=Name")
        if devicesAPI:
            for device in devicesAPI["result"]:  # parse the devices for temperature sensors
                idx = int(device["idx"])
                if idx in self.InTempSensors:
                    if "Temp" in device:
                        Domoticz.Debug("device: {}-{} = {}".format(device["idx"], device["Name"], device["Temp"]))
                        # check temp sensor is not timed out
                        if not self.SensorTimedOut(idx, device["Name"], device["LastUpdate"]):
                            listintemps.append(device["Temp"])
                    else:
                        Domoticz.Error("device: {}-{} is not a Temperature sensor".format(device["idx"], device["Name"]))

        # calculate the average inside temperature
        nbtemps = len(listintemps)
        if nbtemps > 0:
            self.intemp = round(sum(listintemps) / nbtemps, 1)
            Devices[6].Update(nValue = 0,sValue = str(self.intemp),TimedOut = False)
            if self.intemperror:  # there was previously an invalid inside temperature reading... reset to normal
                self.intemperror = False
                self.WriteLog("Inside Temperature reading is now valid again: Resuming normal operation","Status")
                # we remove the timedout flag on the thermostat switch
                Devices[1].Update(nValue = Devices[1].nValue,sValue = Devices[1].sValue,TimedOut = False)
        else:
            # no valid inside temperature
            noerror = False
            if not self.intemperror:
                self.intemperror = True
                Domoticz.Error("No Inside Temperature found: Switching request heating Off")
                self.powerrequestt = False
                # we mark both the thermostat switch and the thermostat temp devices as timedout
                Devices[1].Update(nValue = Devices[1].nValue,sValue = Devices[1].sValue,TimedOut = True)
                Devices[6].Update(nValue = Devices[6].nValue,sValue = Devices[6].sValue,TimedOut = True)


        self.WriteLog("Inside Temperature = {}".format(self.intemp), "Verbose")
        return noerror


    def WriteLog(self, message, level="Normal"):

        if self.loglevel == "Verbose" and level == "Verbose":
            Domoticz.Log(message)
        elif level == "Normal":
            Domoticz.Log(message)

    def SensorTimedOut(self, idx, name, datestring):

        def LastUpdate(datestring):
            dateformat = "%Y-%m-%d %H:%M:%S"
            # the below try/except is meant to address an intermittent python bug in some embedded systems
            try:
                result = datetime.strptime(datestring, dateformat)
            except TypeError:
                result = datetime(*(time.strptime(datestring, dateformat)[0:6]))
            return result

        timedout = LastUpdate(datestring) + timedelta(minutes=int(Settings["SensorTimeout"])) < datetime.now()

        # handle logging of time outs... only log when status changes (less clutter in logs)
        if timedout:
            if self.ActiveSensors[idx]:
                Domoticz.Error("skipping timed out temperature sensor '{}'".format(name))
                self.ActiveSensors[idx] = False
        else:
            if not self.ActiveSensors[idx]:
                Domoticz.Status("previously timed out temperature sensor '{}' is back online".format(name))
                self.ActiveSensors[idx] = True

        return timedout


global _plugin
_plugin = BasePlugin()


def onStart():
    global _plugin
    _plugin.onStart()


def onStop():
    global _plugin
    _plugin.onStop()


def onCommand(Unit, Command, Level, Color):
    global _plugin
    _plugin.onCommand(Unit, Command, Level, Color)


def onHeartbeat():
    global _plugin
    _plugin.onHeartbeat()


# Plugin utility functions ---------------------------------------------------

def parseCSV(strCSV):

    listvals = []
    for value in strCSV.split(","):
        try:
            val = int(value)
        except:
            pass
        else:
            listvals.append(val)
    return listvals


def DomoticzAPI(APICall):

    resultJson = None
    url = "http://{}:{}/json.htm?{}".format(Parameters["Address"], Parameters["Port"], parse.quote(APICall, safe="&="))
    Domoticz.Debug("Calling domoticz API: {}".format(url))
    try:
        req = request.Request(url)
        if Parameters["Username"] != "":
            Domoticz.Debug("Add authentification for user {}".format(Parameters["Username"]))
            credentials = ('%s:%s' % (Parameters["Username"], Parameters["Password"]))
            encoded_credentials = base64.b64encode(credentials.encode('ascii'))
            req.add_header('Authorization', 'Basic %s' % encoded_credentials.decode("ascii"))

        response = request.urlopen(req)
        if response.status == 200:
            resultJson = json.loads(response.read().decode('utf-8'))
            if resultJson["status"] != "OK":
                Domoticz.Error("Domoticz API returned an error: status = {}".format(resultJson["status"]))
                resultJson = None
        else:
            Domoticz.Error("Domoticz API: http error = {}".format(response.status))
    except:
        Domoticz.Error("Error calling '{}'".format(url))
    return resultJson


def CheckParam(name, value, default):

    try:
        param = int(value)
    except ValueError:
        param = default
        Domoticz.Error("Parameter '{}' has an invalid value of '{}' ! defaut of '{}' is instead used.".format(name, value, default))
    return param


# Generic helper functions
def DumpConfigToLog():
    for x in Parameters:
        if Parameters[x] != "":
            Domoticz.Debug("'" + x + "':'" + str(Parameters[x]) + "'")
    Domoticz.Debug("Device count: " + str(len(Devices)))
    for x in Devices:
        Domoticz.Debug("Device:           " + str(x) + " - " + str(Devices[x]))
        Domoticz.Debug("Device ID:       '" + str(Devices[x].ID) + "'")
        Domoticz.Debug("Device Name:     '" + Devices[x].Name + "'")
        Domoticz.Debug("Device nValue:    " + str(Devices[x].nValue))
        Domoticz.Debug("Device sValue:   '" + Devices[x].sValue + "'")
        Domoticz.Debug("Device LastLevel: " + str(Devices[x].LastLevel))
    return