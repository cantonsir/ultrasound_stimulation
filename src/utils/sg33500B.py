import pyvisa
import time
import numpy as np
from os.path import dirname, realpath, join as pjoin
import scipy.io as sio
import serial
import math

def uploadNewUSparameters(centerFreq_kHz = 270, mode = 0, inputmVpp = 100, stimDur_ms = 500,
                          PRF_kHz = 1, dutyCycle = 0.5):
    start = time.time()
    #check parameters are valid
    if centerFreq_kHz<180 or centerFreq_kHz>380 or \
            inputmVpp>350 or stimDur_ms >1000:
        print("Parameters out of range!")
        return -1

    try:
        rm = pyvisa.ResourceManager()
        # print("HERE")
        # print(rm.list_resources())
        sg33500B = rm.open_resource('USB0::0x0957::0x2C07::MY59004380::INSTR')
        print(sg33500B)
        # sg33500B.read_termination = '\n'
        sg33500B.write_termination = '\n'
        sg33500B.write("*RST")  # Reset the function generator
        sg33500B.write("*CLS") # Clear errors and status registers
        sg33500B.write("OUTPut:LOAD 50") #Set the load impedance in Ohms (50 Ohms default)
        #
        sg33500B.write("SOURce2:FUNCtion PULSe")
        sg33500B.write("SOUR2:VOLT 0.100000 VPP")
        sg33500B.write("SOUR2:BURS:INT:PER 10") #in seconds
        sg33500B.write("SOUR2:BURS:MODE TRIG")  #
        sg33500B.write("SOUR2:BURS:STAT ON")  #Enable burst mode

        # UPDATED: Use Internal Trigger for precise timing based on PRF
        # Calculate period in seconds: 1 / (PRF in Hz)
        period_s = 1.0 / (PRF_kHz * 1000.0)
        sg33500B.write("TRIG2:SOUR INT") 
        sg33500B.write(f"SOUR2:BURS:INT:PER {period_s:.6f}")
        # sg33500B.write("TRIG2:SOUR EXT")  # OLD: External trigger (imprecise)
        #
        if mode == 0:
            print('continuous mode')
            sg33500B.write("SOUR2:BURS:NCYC 1")
            sg33500B.write("SOURce2:FREQuency 0.5")
            sg33500B.write("SOUR2:FUNC:PULS:WIDT %f" %float(stimDur_ms / 1000.0)) #convert to second
            print('PULS:WIDT= %s' % float(stimDur_ms / 1000.0))
        elif mode == 1:
            print('pulse mode')
            if PRF_kHz>=10:
                sg33500B.write("SOUR2:FUNC:PULS:WIDT %f" % float(dutyCycle / (PRF_kHz *1000.0)))
                print('PULS:WIDT= %s' % float(dutyCycle / (PRF_kHz *1000.0)))
                sg33500B.write("SOURce2:FREQuency %f" %float(PRF_kHz * 1000.0))
                print('FREQuency= %s' % float(PRF_kHz * 1000.0))
            else:
                sg33500B.write("SOURce2:FREQuency %f" %float(PRF_kHz * 1000.0))
                print('FREQuency= %s' % float(PRF_kHz * 1000.0))
                sg33500B.write("SOUR2:FUNC:PULS:WIDT %f" % float(dutyCycle / (PRF_kHz *1000.0)))
                print('PULS:WIDT= %s' % float(dutyCycle / (PRF_kHz *1000.0)))
 
            sg33500B.write("SOUR2:BURS:NCYC 1")
            # sg33500B.write("SOUR2:BURS:NCYC %d" %round(stimDur_ms * PRF_kHz))
            # print('NCYC= %s' % round(stimDur_ms * PRF_kHz))
        
        sg33500B.write("SOURce1:FUNCtion SINusoid")
        sg33500B.write("SOURce1:FREQuency +%dE+03" % centerFreq_kHz)
        sg33500B.write("SOURce1:VOLTage +%.4f" % float(inputmVpp / 1000.0))
        sg33500B.write("SOUR1:AM 100.000000")
        sg33500B.write("SOURce1:AM:SOURce CH2")
        sg33500B.write("SOUR1:AM:STAT ON")
        sg33500B.write("OUTP1 ON") #Turn on the instrument output
    except:
        print("An exception occurred when uploading parameters to signal generator!")
        return -1

    end = time.time()
    print(end - start)

    return 0

#upload arbitary waveform to signal generator
def uploadArb(ArbWave = 1, inputmVpp = 100, samplefreq = 2.0E6):  
    
    if inputmVpp>350:
        print("Parameters out of range!")
        return -1
    # get matrix
    sig = ArbWave

    # refer to https://github.com/samdejong86/Agilent33600/blob/master/UploadArb.py
    try:
        rm = pyvisa.ResourceManager()
        sg33500B = rm.open_resource('USB0::0x0957::0x2C07::MY59004380::INSTR')
        print(sg33500B)
        sg33500B.timeout = 25000
        sg33500B.write_termination = '\n'
        sg33500B.write("*RST")  # Reset the function generator
        sg33500B.write("*CLS")  # Clear errors and status registers

        #load arbitrary waveform
        sg33500B.write("FORM:BORD SWAP")
        # clear volatile memory
        sg33500B.write("SOUR1:DATA:VOL:CLE")

        #https://docs.python.org/2/library/struct.html#format-characters
        sg33500B.write_binary_values('SOUR1:DATA:ARB arb1,', sig, datatype='f', is_big_endian=False)
        sg33500B.write('*WAI')
        #
        sg33500B.write('SOUR1:FUNC:ARB:SRAT %f' %float(samplefreq))
        sg33500B.write('SOUR1:VOLT:OFFS 0')
        sg33500B.write('SOUR1:FUNC ARB')
        sg33500B.write('SOUR1:FUNC:ARB arb1')
        sg33500B.write("SOUR1:VOLT %f VPP" %float(inputmVpp / 1000.0))
        sg33500B.write('TRIG1:SOUR EXT')
        sg33500B.write("SOUR2:BURS:INT:PER 10")  # in seconds
        sg33500B.write('SOUR1:BURS:STAT ON')
        sg33500B.write('SOUR1:BURS:NCYC 1')
        sg33500B.write('SOUR1:BURS:MODE TRIG')
        sg33500B.write("OUTP1 ON") #Turn on the instrument output

    except:
        print("An exception occurred when uploading parameters to signal generator!")
        return -1

    print("done!")
    return 0

def triggerUS():
    ser = serial.Serial('COM3')
    print("Open Serial")
    # time.sleep(1.5)
    ser.write(b'fUS\n')
    print("Command Sent")
    ack = ser.readline()  # read a '\n' terminated line
    ser.close()
    if ack=="done":
        return 0
    else:
        return -1

def OpenSerial():
    ser = serial.Serial('COM3')
    time.sleep(1.5)
    print("Open Serial")
    return ser

def triggerFUS(ser):
    # time.sleep(1.5)
    ser.write(b'fUS\n')
    # print("Command Sent")
    ack = ser.readline()  # read a '\n' terminated line
    # ser.close()
    # print("Serial port closed")
    # print("from serial:", ack)

    # if str(ack) in ('ACK\n'):
    #     print(ack)
    #     return 0
    # else:
    #     print(ack)
    #     return -1    
    
def truncate(n, decimals=0):
    multiplier = 10 ** decimals
    return int(n * multiplier) / multiplier

def round_half_up(n, decimals=0):
    multiplier = 10 ** decimals
    return math.floor(n*multiplier + 0.5) / multiplier
def turnOff():
    try:
        rm = pyvisa.ResourceManager()
        sg33500B = rm.open_resource('USB0::0x0957::0x2C07::MY59004380::INSTR')
        sg33500B.write_termination = '\n'
        sg33500B.write("OUTP1 OFF")
        sg33500B.close()
        print("Output turned off successfully.")
        return 0
    except Exception as e:
        print(f"Failed to turn off signal generator: {e}")
        return -1
