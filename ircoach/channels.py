"""Telemetrie-Kanaele, die aufgezeichnet und analysiert werden."""

# Kanaele, die pro Tick aus dem SDK gelesen werden. Fehlende Kanaele
# (fahrzeugabhaengig) werden still uebersprungen.
CHANNELS = [
    "SessionTime", "SessionNum", "Lap", "LapDist", "LapDistPct",
    "LapCurrentLapTime", "LapLastLapTime", "LapBestLapTime",
    "Speed", "Throttle", "Brake", "Clutch", "Gear", "RPM",
    "SteeringWheelAngle", "SteeringWheelTorque",
    "LatAccel", "LongAccel", "VertAccel", "YawRate",
    "BrakeABSactive", "PlayerTrackSurface", "IsOnTrack", "OnPitRoad",
    "FuelLevel", "TrackTemp",
    "Lat", "Lon", "Alt", "Yaw",
    "LFspeed", "RFspeed", "LRspeed", "RRspeed",
    "LFbrakeLinePress", "RFbrakeLinePress",
    "dcTractionControl", "dcABS", "dcBrakeBias", "ShiftIndicatorPct",
]

# Kanaele, die auf das Distanzraster interpoliert werden.
GRID_CHANNELS = [
    "t", "Speed", "Throttle", "Brake", "Gear", "RPM",
    "SteeringWheelAngle", "LatAccel", "LongAccel", "YawRate",
    "BrakeABSactive", "LFspeed", "RFspeed", "LRspeed", "RRspeed",
    "PlayerTrackSurface", "Lat", "Lon", "Yaw", "dcBrakeBias",
]

# Kanaele, die fuer den Linienvergleich noetig sind.
POS_CHANNELS = ("Lat", "Lon")

# iRacing TrackSurface-Enum
SURF_OFFTRACK = 0
SURF_PIT_STALL = 1
SURF_APPROACHING_PITS = 2
SURF_ONTRACK = 3
