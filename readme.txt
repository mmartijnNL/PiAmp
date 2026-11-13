Running PiAmp
Start with ./start_manual.sh or python3 -m app.main.
PiAmp stays running and checks config.yaml and state/routes.json every second.
Saved changes are validated and applied without restarting or rewriting either file.
Invalid edits are reported and are not applied; save corrected files to retry.
Empty zone route maps disconnect that zone's routes. Deleted or moved zones have
their old links cleared on reload. Press Ctrl+C to stop.
Zones sharing a multichannel device are reconciled together, preserving each
zone's connections on reapply. Missing channels, failed PipeWire commands, and
links that do not persist are reported as errors rather than successful routes.

Desktop autostart
install_service.sh installs a desktop autostart entry, not a headless service.
It takes effect on the next desktop login. Bare Openbox sessions also require
PyXDG to process desktop autostart entries. If openbox-xdg-autostart reports
"requires PyXDG to be installed", run: sudo apt install python3-xdg

Terminal audio meter
On X11, start_manual.sh disables screen blanking and display power saving for
the current desktop session, so an idle PiAmp display stays visible. This also
affects other windows in that session and lasts until logout. To restore the
usual 10-minute timeout manually, run: xset s 600 +dpms
Wayland power-saving settings are unchanged. Failures are printed as warnings.

Run python3 -m app.meter to show live INPUT/OUTPUT dBFS levels for the
UMC1820's multichannel capture and playback nodes, refreshed in place in the
terminal. Numeric levels are rounded to whole dBFS, without decimals.
Requires pw-record and numpy (see requirements.txt). Optional flags:
--rate, --frames, and --refresh. Press Ctrl+C to stop.

Step-by-step plan
Phase 1: Domain model and config
Define the core objects:
Input
Zone
Route
MixState
Command payload
Create a config schema that supports:
zone names
input names
channel assignments
stereo/mono mapping
default route presets
Validate bad config early: invalid zone names, duplicate IDs, impossible channel mappings, unknown inputs.
Phase 2: PipeWire adapter
Detect the UMC1820 node and its channel layout.
Discover target output devices and zone associations.
Build helpers for:
enumerating nodes
reading channel position metadata
creating/adjusting links
changing gain on route or mixer stages
Phase 3: Routing and mixing engine
Implement a route matrix:
input → zone
zone → output
gain per route
Support:
single input to many zones
many inputs to one zone
weighted mixing
zone-specific volume control
Provide command resolution for:
all zones
selected zones
zone groups
default presets
Phase 4: JSON command API
Expose endpoints for:
status
route updates
route deletion
presets
config reload
Example payloads:
route input to all zones
route multiple inputs to multiple zones in one command
set gain for each route independently
Phase 5: Home Assistant bridge
Publish zone state and route state.
Support HA service calls for:
play source to zone(s)
switch zone source
adjust zone volume
recall scene/preset
Keep HA as a client layer; the Pi service remains authoritative.
Phase 6: Production hardening
Add process supervision and config reload logic.
Add logging, health checks, and watchdog behavior.
Add tests for config parsing, route resolution, and mixer logic.
Validate on the actual Pi with PipeWire tools and real UMC1820 channel layouts.
Verification plan
Unit tests for config validation and route matrix logic.
Simulated PipeWire graph tests to validate link and gain changes.
Real Pi validation using PipeWire status and channel inspection.
End-to-end JSON API tests for route commands.
HA smoke test confirming commands and state updates flow correctly.
Decision summary
Use PipeWire rather than direct ALSA hacks for routing and gain control.
Keep routing declarative and human-readable.
Use MQTT for Home Assistant control unless a local REST API is preferred.
Design the service around zone-based mixing from the start; this is the key to supporting “play TV to all zones” and “mix input A/B into Y/Z” without brittle one-off logic.
This repo is currently empty, so the next practical implementation is to scaffold the Python package, define the config format, and build the PipeWire adapter before wiring in the API and HA layer.

If you want, I can move directly from this plan into project scaffolding and the first working Python service skeleton.


rpi@rpi:~ $ wpctl status
PipeWire 'pipewire-0' [1.4.2, rpi@rpi, cookie:4267863899]
 └─ Clients:
        33. WirePlumber                         [1.4.2, rpi@rpi, pid:1301]
        34. pipewire                            [1.4.2, rpi@rpi, pid:1306]
        47. WirePlumber [export]                [1.4.2, rpi@rpi, pid:1301]
        84. xdg-desktop-portal                  [1.4.2, rpi@rpi, pid:1544]
        85. xdg-desktop-portal-wlr              [1.4.2, rpi@rpi, pid:1613]
        86. unknown                             [1.4.2, rpi@rpi, pid:1478]
       126. wpctl                               [1.4.2, rpi@rpi, pid:2358]

Audio
 ├─ Devices:
 │      48. Built-in Audio                      [alsa]
 │      49. Built-in Audio                      [alsa]
 │      50. Built-in Audio                      [alsa]
 │      89. UMC1820                             [alsa]
 │  
 ├─ Sinks:
 │      35. Built-in Audio Digital Stereo (HDMI) [vol: 0.40]
 │  *   57. Built-in Audio Stereo               [vol: 0.40]
 │      90. UMC1820 Multichannel                [vol: 1.00]
 │  
 ├─ Sources:
 │  *   91. UMC1820 Multichannel                [vol: 1.00]
 │  
 ├─ Filters:
 │  
 └─ Streams:

Video
 ├─ Devices:
 │      43. bcm2835-codec-isp                   [v4l2]
 │      44. bcm2835-codec-decode                [v4l2]
 │      45. bcm2835-codec-encode                [v4l2]
 │      46. rpi-hevc-dec                        [v4l2]
 │      58. bcm2835-codec-image_fx              [v4l2]
 │      59. bcm2835-codec-encode_image          [v4l2]
 │      60. bcm2835-isp                         [v4l2]
 │      61. bcm2835-isp                         [v4l2]
 │      62. bcm2835-isp                         [v4l2]
 │      63. bcm2835-isp                         [v4l2]
 │      64. bcm2835-isp                         [v4l2]
 │      65. bcm2835-isp                         [v4l2]
 │      66. bcm2835-isp                         [v4l2]
 │      67. bcm2835-isp                         [v4l2]
 │  
 ├─ Sinks:
 │  
 ├─ Sources:
 │  *   76. bcm2835-isp (V4L2)                 
 │      78. bcm2835-isp (V4L2)                 
 │      80. bcm2835-isp (V4L2)                 
 │      82. bcm2835-isp (V4L2)                 
 │  
 ├─ Filters:
 │  
 └─ Streams:

Settings
 └─ Default Configured Devices:
rpi@rpi:~ $ ^C
rpi@rpi:~ $ 


rpi@rpi:~ $ ^C
rpi@rpi:~ $ pw-cli info 91
        id: 91
        permissions: rwxm-
        type: PipeWire:Interface:Node/3
*       input ports: 0/0
*       output ports: 10/65
*       state: "suspended"
*       properties:
*               alsa.card = "3"
*               alsa.card_name = "UMC1820"
*               alsa.class = "generic"
*               alsa.components = "USB1397:0514"
*               alsa.device = "0"
*               alsa.driver_name = "snd_usb_audio"
*               alsa.id = "USB Audio"
*               alsa.long_card_name = "BEHRINGER UMC1820 at usb-0000:01:00.0-1.2, high speed"
*               alsa.mixer_name = "USB Mixer"
*               alsa.name = "USB Audio"
*               alsa.resolution_bits = "32"
*               alsa.subclass = "generic-mix"
*               alsa.subdevice = "0"
*               alsa.subdevice_name = "subdevice #0"
*               alsa.sync.id = "00000000:00000000:00000000:00000000"
*               api.alsa.card.longname = "BEHRINGER UMC1820 at usb-0000:01:00.0-1.2, high speed"
*               api.alsa.card.name = "UMC1820"
*               api.alsa.path = "hw:3"
*               api.alsa.pcm.card = "3"
*               api.alsa.pcm.stream = "capture"
*               audio.channels = "10"
*               audio.position = "AUX0,AUX1,AUX2,AUX3,AUX4,AUX5,AUX6,AUX7,AUX8,AUX9"
*               card.profile.device = "2"
*               device.api = "alsa"
*               device.class = "sound"
*               device.icon-name = "audio-card-analog"
*               device.id = "89"
*               device.profile.description = "Multichannel"
*               device.profile.name = "multichannel-input"
*               device.routes = "0"
*               factory.name = "api.alsa.pcm.source"
*               media.class = "Audio/Source"
*               node.description = "UMC1820 Multichannel"
*               node.name = "alsa_input.usb-BEHRINGER_UMC1820_7A122C9D-00.multichannel-input"
*               node.nick = "UMC1820"
*               node.pause-on-idle = "false"
*               object.path = "alsa:acp:UMC1820:2:capture"
*               port.group = "capture"
*               priority.driver = "2000"
*               priority.session = "2000"
*               factory.id = "19"
*               clock.quantum-limit = "8192"
*               client.id = "47"
*               node.driver = "true"
*               node.loop.name = "data-loop.0"
*               library.name = "audioconvert/libspa-audioconvert"
*               object.id = "91"
*               object.serial = "96"
*       params: (9)
*         3 (Spa:Enum:ParamId:EnumFormat) r-
*         1 (Spa:Enum:ParamId:PropInfo) r-
*         2 (Spa:Enum:ParamId:Props) rw

*         4 (Spa:Enum:ParamId:Format) -w
*         10 (Spa:Enum:ParamId:EnumPortConfig) r-
*         11 (Spa:Enum:ParamId:PortConfig) rw
*         15 (Spa:Enum:ParamId:Latency) rw
*         16 (Spa:Enum:ParamId:ProcessLatency) rw
*         17 (Spa:Enum:ParamId:Tag) rw
rpi@rpi:~ $ ^C
rpi@rpi:~ $ 
