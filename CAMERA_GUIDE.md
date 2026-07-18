# Camera Requirements for Cricket DRS

## Overview

The Cricket DRS system supports multiple camera input types. Choose based on your testing scenario:

| Scenario | Camera Type | Setup Time | Cost |
|----------|------------|-----------|------|
| **Quick Testing** | Webcam / USB camera | 5 min | $20-100 |
| **Video File Testing** | MP4/AVI files | 1 min | Free (use existing videos) |
| **Live Capture (Tournament)** | Professional HDcameras | 2-3 hours | $5000+ |

---

## Option 1: Quick Testing (Recommended First)

### **Use Pre-Generated Synthetic Videos** ✅ EASIEST

```powershell
python scripts/generate_test_video.py --output test_delivery.mp4 --duration 10
python drs_app.py --testing-api --host 127.0.0.1 --port 8766
```

**Advantages:**
- ✅ No hardware needed
- ✅ Instant video generation
- ✅ Consistent ball motion for testing
- ✅ Perfect for verifying pipeline works

**Disadvantages:**
- Synthetic data (not real cricket)
- Slower than live capture for long sessions

---

## Option 2: Webcam / USB Camera (Simple)

### **What You Need:**
- 1-6 USB webcams or capture cards
- USB hub (if multiple cameras)
- Computer with 2+ USB 3.0 ports

### **Supported Formats:**
- **USB Webcam** (Logitech, Microsoft, etc.)
- **USB Video Capture Card** (Elgato, AVerMedia)
- **IP Webcam** (via USB or network)

### **Specifications for Good Results:**

| Property | Requirement | Recommended | Why |
|----------|------------|-------------|-----|
| **Resolution** | 640x480 min | 1280x720+ | Better ball detection |
| **FPS** | 24 min | 30-60 FPS | Smoother tracking |
| **Latency** | < 100ms | < 50ms | Real-time sync |
| **Sensor** | Any | Rolling shutter OK | OpenCV compatible |
| **Codec** | H.264/MJPEG | H.264 | Standard support |

### **Setup:**

1. **Connect cameras to USB ports**
   ```powershell
   # List available cameras
   python drs_app.py --list-cameras
   ```
   Output:
   ```
   camera 0: OK  1280x720  fps=30.0
   camera 1: OK  1280x720  fps=30.0
   ```

2. **Run with multiple cameras**
   ```powershell
   python drs_app.py --cameras 0,1 --record --headless --seconds 60
   ```

3. **View real-time output:**
   - Synchronized frames from all cameras
   - Ball detection and tracking overlays
   - FPS and sync metrics

### **Pros & Cons:**

✅ **Pros:**
- Low cost ($20-100 per camera)
- Plug & play
- Real-time processing
- Multiple camera support

❌ **Cons:**
- Consumer-grade accuracy
- Limited field of view
- May need mounting solutions
- Sync may drift over long periods (> 1 hour)

---

## Option 3: Video File Upload (No Hardware Needed)

### **Easiest for Testing**

Upload MP4 files directly via web interface:

```powershell
# 1. Start backend
python drs_app.py --testing-api --host 127.0.0.1 --port 8766

# 2. Start frontend
cd dashboard/testing-platform && npm run dev

# 3. Upload your video
# - Open http://localhost:5173/
# - Click "Upload Delivery"
# - Select any MP4 file
# - Click "Process Delivery"
```

### **Supported Video Formats:**

| Format | Codec | Container | Supported |
|--------|-------|-----------|-----------|
| MP4 | H.264 | MP4 | ✅ Yes |
| MOV | H.264 | MOV | ✅ Yes |
| AVI | H.264/MPEG4 | AVI | ✅ Yes |
| MKV | H.264 | MKV | ✅ Yes |
| WebM | VP9 | WebM | ⚠️ May work |

### **Video Specifications:**

```
Resolution:  1280x720 recommended (640x480 min, 4K max)
FPS:         24-60 FPS
Duration:    10 seconds to 10 minutes (tested)
Bitrate:     2-10 Mbps
Codec:       H.264/AVC preferred
File size:   Up to 500MB per upload
```

### **Example Commands:**

```powershell
# Test with an existing cricket video
python drs_app.py --testing-api

# In web interface:
# 1. Upload: my_cricket_delivery.mp4
# 2. Wait 30 seconds for analysis
# 3. View results

# For dual-camera analysis:
# 1. Upload: camera_1.mp4
# 2. Upload: camera_2.mp4 (same delivery, different angle)
# 3. System syncs and analyzes both
```

### **Pros & Cons:**

✅ **Pros:**
- No hardware required
- Test with existing videos
- Works on any computer
- Can process 2+ videos for stereo analysis

❌ **Cons:**
- Not live streaming
- Limited to available videos
- Processing takes time (30+ seconds per delivery)

---

## Option 4: Professional Multi-Camera Setup (Tournament)

### **For 2-3 Hour Live Testing**

### **Recommended Setup:**

```
┌─────────────────────────────────────────┐
│     6x Professional HD Cameras          │
│    (2000D/C200/GY-HM600 series)        │
│     @ 1920x1080 @ 60 FPS              │
└────────────┬────────────────────────────┘
             │
      SDI → HDMI Converters
             │
      USB/HDMI Capture Cards
             │
      ┌──────┴──────────────────┐
      │   Capture PC/Laptop     │
      │   (Intel i7, 32GB RAM)  │
      │   2-4 USB 3.0 ports    │
      └──────────────────────────┘
             │
      ┌──────┴──────────────────┐
      │  FastAPI Backend        │
      │  + WebSocket sync       │
      └──────────────────────────┘
```

### **Hardware Specifications:**

| Component | Specification | Cost |
|-----------|--------------|------|
| **Cameras** | 6x 4K @ 60 FPS | $3000-6000 |
| **Capture Cards** | 3-6x HDMI USB 3.0 | $1500-3000 |
| **Network** | 1 Gbps+ Ethernet | $200 |
| **Computer** | i7+, 32GB RAM, SSD | $1500-2500 |
| **Sync Hardware** | GPS/PPS sync module | $500+ |
| **Total Cost** | — | **$7000-15000** |

### **Software Configuration:**

```powershell
# Run with 6 cameras
python drs_app.py --cameras 0,1,2,3,4,5 --api --record

# Start Electron dashboard
cd dashboard/electron && npm start

# Expected throughput:
# - 6 x 1920x1080 @ 60 FPS = ~6 Gbps data
# - YOLO11X detection: ~800ms per frame
# - Multi-camera sync: ±2ms tolerance
# - Record to SSD: 100+ MB/second
```

### **Synchronization:**

For tournament-grade accuracy:

1. **Camera Sync Tolerance:** ±2ms (configurable in `.env`)
2. **Frame Drop Detection:** Automatic
3. **Audio Sync:** UltraEdge analysis for edge detection
4. **Calibration:** Required (15+ ChArUco board images per camera)

### **Pros & Cons:**

✅ **Pros:**
- Tournament-grade accuracy
- Real-time live processing
- Multi-angle stereo analysis
- Professional HDI/SDI support
- 2-3 hour continuous capture

❌ **Cons:**
- Expensive ($7000-15000)
- Requires technical setup
- Needs calibration for each venue
- Complex synchronization
- Not suitable for quick testing

---

## Quick Recommendation Flow

```
┌─ Start Here ──────────────────────┐
│                                    │
│ 1. Do you have hardware?           │
│    YES → Use webcam (Option 2)     │
│    NO  → Skip to step 2            │
│                                    │
│ 2. Do you have cricket videos?     │
│    YES → Upload via web (Option 3) │
│    NO  → Generate synthetic (opt 1)│
│                                    │
│ 3. Testing purpose?                │
│    Debug/Dev → Synthetic (opt 1)   │
│    Real data → Your video (opt 3)  │
│    Tournament → Pro setup (opt 4)  │
│                                    │
└────────────────────────────────────┘
```

---

## Testing Checklist

### Before Starting:

- [ ] Python 3.11+ installed
- [ ] Dependencies installed: `pip install -r requirements.txt`
- [ ] Package installed: `pip install -e .`
- [ ] Frontend dependencies: `cd dashboard/testing-platform && npm install`

### Quick Test (Synthetic Video):

```powershell
# 1. Generate video (1 min)
python scripts/generate_test_video.py --output test.mp4 --duration 10

# 2. Start backend (30 sec)
python drs_app.py --testing-api --host 127.0.0.1 --port 8766

# 3. Start frontend (10 sec)
cd dashboard/testing-platform && npm run dev

# 4. Test (2 min)
# - Open http://localhost:5173/
# - Upload test.mp4
# - Wait for results
```

**Total time:** ~4-5 minutes ✅

### Multi-Camera Test (With Webcams):

```powershell
# List cameras
python drs_app.py --list-cameras

# Run with 2 cameras
python drs_app.py --cameras 0,1 --headless --seconds 30

# Check output
ls data/recordings/
```

---

## Troubleshooting Camera Issues

### "No cameras opened"

```powershell
# Windows: Check Device Manager → Cameras
# Linux: lsusb | grep camera
# Mac: system_profiler SPCameraDataType

# Fix: Try different camera indices
python drs_app.py --list-cameras --scan-limit 20
```

### "Camera frames are blurry"

- Increase light (50+ lux recommended)
- Clean camera lens
- Reduce motion blur (increase FPS or shutter speed)
- Move camera closer to delivery area

### "Sync spread too high"

- Reduce resolution (1280x720 → 640x480)
- Reduce FPS (60 → 30)
- Use shorter USB cables
- Check USB bus bandwidth with `lsof` or Task Manager

---

## Next Steps

1. **Start with Option 1** (Synthetic video) - 5 minutes
2. **Test with Option 3** (Upload video file) - No hardware needed
3. **Graduate to Option 2** (Webcam) - Simple setup
4. **Plan Option 4** (Professional) - For tournament use

---

**Questions?** See `QUICK_START.md` or `TESTING_GUIDE.md` for more details.
