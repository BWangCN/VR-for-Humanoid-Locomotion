using System;
using System.IO;
using UnityEngine;

public class SMPLXRealtimeBinder_FPV : MonoBehaviour
{

    [Header("Yaw from 24 Joints (live)")]
    public bool driveYawFromJoints = true;      // 开：实时由24点驱动yaw
    [Range(0f, 1f)] public float jointsYawEma = 0.15f;  // yaw低通(EMA)系数
    public float jointsYawMaxStepDeg = 30f;     // 每帧最大yaw步进（度）
    public bool fallbackUseShouldersIfHipsDegenerate = true;

    private float _yawFilteredDeg = 0f;
    private bool _yawInit = false;

    [Header("References")]
    public PXR_BodyTrackingFeed feed;      // 24点来源
    public RealTimeSMPLX smplx;            // 你的 SMPL-X 组件
    public Transform smplxRoot;            // SMPL-X 根（整体位姿控制）
    public Transform xrCamera;             // XR 相机（HMD）

    [Header("Tracking Origin (PICO local -> Unity world)")]
    public Transform trackingOrigin;       // 一般设为 feed.cubesRoot
    public bool feedIsLocalSpace = true;   // feed里pos是否是local

    [Header("XR Origin Frame (NEW)")]
    [Tooltip("XR Origin（或 Camera Offset）的 Transform。启动时可把 trackingOrigin 对齐到它。")]
    public Transform xrOrigin;
    public bool alignTrackingToXROnStart = true; // 启动就把trackingOrigin对齐到xrOrigin
    public bool trackingAlignYawOnly = true;     // 只对齐水平朝向更稳
    public Vector3 trackingOriginPosOffset = Vector3.zero; // 对齐后附加偏移（米），例如(0,0,0)

    [Header("Mapping (Feed -> SMPL24)")]
    public int[] roleToSmpl24 = new int[24] {
        0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23
    };

    [Header("Eye Anchor as Head + Offset (no eye bones)")]
    public Vector3 headFallbackEyeLocal = new Vector3(0f, 0.07f, 0.10f); // 眼睛相对头骨偏移
    public bool debugDrawEyeAnchor = false;

    [Header("Align SMPLX to XR Camera (FPV)")]
    public bool alignHeadToCamera = true;
    public bool alignHeadRotation = true;
    public bool yawOnly = true;
    public float maxPosStep = 0.3f;   // 每帧最大位移
    public float maxDegStep = 25f;    // 每帧最大旋转（度）
    public bool snapCalibrateOnStart = true;
    public KeyCode snapCalibrateHotkey = KeyCode.F8;
    [Header("Rotation Handoff")]
    [Tooltip("初始化（或按热键）后就不再每帧旋转，只做平移，让朝向完全由24点驱动。")]
    public bool lockRotationAfterInit = true;

    [Header("Scale Calibration (optional)")]
    public bool autoCalibrateScale = false;
    public float targetEyeHeight = 0f;    // 0=用相机y
    public float scaleLerp = 0.2f;

    [Header("First-Person Culling")]
    public bool hideHeadForLocalCamera = true;
    public string selfLayerName = "Self";
    public Renderer[] headRenderers;

    [Header("Recording (optional)")]
    public bool recordToFile = false;
    public string fileBaseName = "smplx_24pts";
    public bool csvFormat = true;
    public int writeEveryNFrames = 1;
    private StreamWriter _writer;
    private string _filePath;
    private int _frameCounter;

    // internals
    private Vector3[] J24 = new Vector3[24];
    private Transform headT;
    private bool headReady;
    private bool _rotationLocked = false;

    void Awake()
    {
        if (smplxRoot == null && smplx != null) smplxRoot = smplx.transform;
    }

    void Start()
    {
        // ① 启动时：把 trackingOrigin 对齐到 XR Origin（位置+朝向）
        if (alignTrackingToXROnStart && trackingOrigin != null && xrOrigin != null)
            AlignTrackingOriginToXROrigin(trackingAlignYawOnly, trackingOriginPosOffset);

        // 头骨引用
        headT = smplx.JointPoints[(int)PositionIndex.head]?.Transform;
        headReady = (headT != null);

        if (hideHeadForLocalCamera) ApplySelfLayerToHead();
        if (recordToFile) BeginRecord();

        // ② 启动时：把 SMPLX（眼睛=头+偏移）吸到 XR 相机处
        //if (snapCalibrateOnStart) SnapCalibrateToCamera();
        if (snapCalibrateOnStart)
        {
            SnapCalibrateToCamera();
            if (lockRotationAfterInit) _rotationLocked = true;
        }
    }

    void OnDestroy()
    {
        try { _writer?.Flush(); _writer?.Close(); } catch { }
    }

    void Update()
    {
#if UNITY_ANDROID
        if (feed == null || smplx == null) return;
        var poses = feed.LatestRolePoses;
        if (poses == null || poses.Length == 0) return;

        //if (Input.GetKeyDown(snapCalibrateHotkey)) SnapCalibrateToCamera();
        if (Input.GetKeyDown(snapCalibrateHotkey))
        {
            SnapCalibrateToCamera();              // 允许用户主动“旋转一次”
            if (lockRotationAfterInit) _rotationLocked = true;
        }



        // 1) 拉取24点（local->world 取决于 feedIsLocalSpace）
        for (int i = 0; i < 24; i++)
        {
            int src = (i < roleToSmpl24.Length) ? roleToSmpl24[i] : i;
            Vector3 pLocalOrWorld = (src >= 0 && src < poses.Length && poses[src].valid) ? poses[src].pos : Vector3.zero;
            J24[i] = (feedIsLocalSpace && trackingOrigin != null) ? trackingOrigin.TransformPoint(pLocalOrWorld) : pLocalOrWorld;
        }

        // 2) 喂给 SMPL-X（世界坐标）
        smplx.ApplySMPL24(
            J24[0], J24[1], J24[2], J24[3],
            J24[4], J24[5],
            J24[6], J24[7], J24[8], J24[9],
            J24[10], J24[11],
            J24[12],
            J24[13], J24[14],
            J24[15],
            J24[16], J24[17],
            J24[18], J24[19],
            J24[20], J24[21],
            J24[22], J24[23],
            headWorldRot: null, lWristWorldRot: null, rWristWorldRot: null
        );
        // 2.5) 由24点实时驱动根节点 yaw（只旋转水平朝向）
        if (driveYawFromJoints && smplxRoot != null)
            UpdateRootYawFromJoints();


        // 3) 眼睛吸附到相机（第一人称）
        //if (alignHeadToCamera && headReady && xrCamera != null && smplxRoot != null)
        //    AlignHeadOffsetToCamera();
        if (alignHeadToCamera && headReady && xrCamera != null && smplxRoot != null)
            AlignHeadOffsetToCamera_NoRotate();


        // 4) 可选：按眼高校准整体scale
        if (autoCalibrateScale && headReady)
            CalibrateScaleToEyeHeight();

        // 5) 录制
        if (recordToFile && (++_frameCounter % writeEveryNFrames == 0)) WriteFrame();

        // debug
        if (debugDrawEyeAnchor && headReady)
        {
            Vector3 eyesWorld = headT.TransformPoint(headFallbackEyeLocal);
            Debug.DrawLine(eyesWorld, eyesWorld + headT.forward * 0.15f, Color.cyan);
            Debug.DrawLine(eyesWorld, eyesWorld + headT.up * 0.12f, Color.green);
            Debug.DrawLine(eyesWorld, eyesWorld + headT.right * 0.12f, Color.red);
        }
#endif
    }
    // 根据24点计算“身体forward”，投影到XZ得到目标yaw，并以EMA+步进限制更新 smplxRoot 的水平朝向
    private void UpdateRootYawFromJoints()
    {
        // ① 取关键点（世界坐标）：颈部、左右髋（必要时可回退到左右肩）
        Vector3 neck = J24[(int)PositionIndex.neck];
        Vector3 lHip = J24[(int)PositionIndex.left_hip];
        Vector3 rHip = J24[(int)PositionIndex.right_hip];

        // 基于三点的法线作为“身体forward”（与你的 RealTimeSMPLX.TriangleNormal 一致）
        Vector3 forward = TriangleNormal(neck, lHip, rHip);  // 方向: 面-肚皮朝前

        // ② 退化与回退：若髋三点共线或数据抖成零向量，可选用“颈+左右肩”
        if (forward.sqrMagnitude < 1e-6f && fallbackUseShouldersIfHipsDegenerate)
        {
            Vector3 lSh = J24[(int)PositionIndex.left_shoulder];
            Vector3 rSh = J24[(int)PositionIndex.right_shoulder];
            forward = TriangleNormal((lSh + rSh) * 0.5f, lSh, rSh);
        }
        if (forward.sqrMagnitude < 1e-6f) return; // 还是不行就放弃本帧

        // ③ 只取 yaw：把forward投影到水平面
        Vector3 fXZ = Vector3.ProjectOnPlane(forward, Vector3.up).normalized;
        if (fXZ.sqrMagnitude < 1e-6f) return;

        // ④ 目标yaw（度）。注意Unity里“朝向Z正”为0°，用 atan2(x,z)
        float desiredYawDeg = Mathf.Rad2Deg * Mathf.Atan2(fXZ.x, fXZ.z);

        // ⑤ EMA 平滑到 _yawFilteredDeg
        if (!_yawInit)
        {
            _yawFilteredDeg = desiredYawDeg;
            _yawInit = true;
        }
        else
        {
            _yawFilteredDeg = Mathf.LerpAngle(_yawFilteredDeg, desiredYawDeg, jointsYawEma);
        }

        // ⑥ 当前yaw
        float currentYawDeg = smplxRoot.eulerAngles.y;

        // ⑦ 限制单帧步进，避免瞬转/穿越
        float deltaDeg = Mathf.DeltaAngle(currentYawDeg, _yawFilteredDeg);
        deltaDeg = Mathf.Clamp(deltaDeg, -jointsYawMaxStepDeg, jointsYawMaxStepDeg);

        // ⑧ 绕世界Y轴转动根节点（仅水平）
        if (Mathf.Abs(deltaDeg) > 1e-3f)
            smplxRoot.rotation = Quaternion.AngleAxis(deltaDeg, Vector3.up) * smplxRoot.rotation;
    }

    // 与 RealTimeSMPLX 相同的三角法线
    private Vector3 TriangleNormal(Vector3 a, Vector3 b, Vector3 c)
    {
        Vector3 d1 = a - b;
        Vector3 d2 = a - c;
        Vector3 n = Vector3.Cross(d1, d2);
        float m = n.magnitude;
        return (m > 1e-6f) ? (n / m) : Vector3.zero;
    }


    // ---------- NEW: trackingOrigin ↔ XR Origin 对齐 ----------
    public void AlignTrackingOriginToXROrigin(bool yawOnlyAlign, Vector3 posOffset)
    {
        if (trackingOrigin == null || xrOrigin == null) return;

        // 位置：直接贴到 xrOrigin（可加偏移）
        trackingOrigin.position = xrOrigin.position + posOffset;

        // 朝向：全朝向或仅 yaw
        if (yawOnlyAlign)
        {
            Vector3 f = Vector3.ProjectOnPlane(xrOrigin.forward, Vector3.up).normalized;
            if (f.sqrMagnitude < 1e-6f) f = Vector3.forward;
            trackingOrigin.rotation = Quaternion.LookRotation(f, Vector3.up);
        }
        else
        {
            trackingOrigin.rotation = xrOrigin.rotation;
        }
    }

    // ---------- FPV: 头+偏移 对齐相机 ----------
    private void AlignHeadOffsetToCamera_NoRotate()
    {

        Vector3 eyesWorld = headT.TransformPoint(headFallbackEyeLocal);
        Vector3 delta = xrCamera.position - eyesWorld;
        float step = delta.magnitude;
        if (step > maxPosStep) delta = delta.normalized * maxPosStep;
        smplxRoot.position += delta;
    }

    public void SnapCalibrateToCamera()
    {
        if (!headReady || xrCamera == null || smplxRoot == null) return;

        // 旋转瞬贴
        //Quaternion headRot = headT.rotation;
        //Quaternion camRot = xrCamera.rotation;
        //if (alignHeadRotation)
        Quaternion headRot = headT.rotation;
        Quaternion camRot = xrCamera.rotation;
        if (alignHeadRotation)
        {
            if (yawOnly)
            {
                Vector3 headF = Vector3.ProjectOnPlane(headRot * Vector3.forward, Vector3.up).normalized;
                Vector3 camF = Vector3.ProjectOnPlane(camRot * Vector3.forward, Vector3.up).normalized;
                if (headF.sqrMagnitude > 1e-6f && camF.sqrMagnitude > 1e-6f)
                {
                    Quaternion deltaYaw = Quaternion.FromToRotation(headF, camF);
                    smplxRoot.rotation = deltaYaw * smplxRoot.rotation;
                }
            }
            else
            {
                Quaternion deltaR = camRot * Quaternion.Inverse(headRot);
                smplxRoot.rotation = deltaR * smplxRoot.rotation;
            }
        }

        // 位置瞬贴
        Vector3 eyesWorld = headT.TransformPoint(headFallbackEyeLocal);
        Vector3 delta = xrCamera.position - eyesWorld;
        smplxRoot.position += delta;
    }

    private void ApplyClampedRotation(Quaternion deltaR)
    {
        if (_rotationLocked) return;
        deltaR.ToAngleAxis(out float angle, out Vector3 axis);
        angle = Mathf.DeltaAngle(0f, angle);
        float clamped = Mathf.Clamp(angle, -maxDegStep, maxDegStep);
        Quaternion stepR = Quaternion.AngleAxis(clamped, axis);
        smplxRoot.rotation = stepR * smplxRoot.rotation;
    }

    // ---------- Scale ----------
    private void CalibrateScaleToEyeHeight()
    {
        float targetY = (targetEyeHeight > 0f) ? targetEyeHeight : xrCamera.position.y;
        float modelEyeY = headT.TransformPoint(headFallbackEyeLocal).y;

        float s = smplxRoot.localScale.x;
        if (Mathf.Abs(modelEyeY) < 1e-4f || s <= 0f) return;

        float desiredS = s * (targetY / modelEyeY);
        float newS = Mathf.Lerp(s, desiredS, scaleLerp);
        smplxRoot.localScale = new Vector3(newS, newS, newS);
    }

    // ---------- First-person culling ----------
    private void ApplySelfLayerToHead()
    {
        int selfLayer = LayerMask.NameToLayer(selfLayerName);
        if (selfLayer < 0)
        {
            Debug.LogWarning($"[FPV] Layer '{selfLayerName}' not found. Create it and exclude from XR Camera.");
            return;
        }
        foreach (var r in headRenderers)
        {
            if (r == null) continue;
            SetLayerRecursively(r.gameObject, selfLayer);
        }
        // 确保 XR Camera 的 Culling Mask 取消勾选 Self 层
    }
    private void SetLayerRecursively(GameObject go, int layer)
    {
        go.layer = layer;
        foreach (Transform c in go.transform) SetLayerRecursively(c.gameObject, layer);
    }

    // ---------- Recording ----------
    private void BeginRecord()
    {
        try
        {
            string ts = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string ext = csvFormat ? "csv" : "txt";
            _filePath = Path.Combine(Application.persistentDataPath, $"{fileBaseName}_{ts}.{ext}");
            _writer = new StreamWriter(_filePath, false, System.Text.Encoding.UTF8);

            if (csvFormat)
            {
                _writer.Write("frame,time");
                for (int i = 0; i < 24; i++) _writer.Write($",p{i}x,p{i}y,p{i}z");
                _writer.WriteLine();
            }
            else
            {
                _writer.WriteLine("# smplx 24 points stream");
            }
            _writer.Flush();
            Debug.Log($"[FPV] Recording to: {_filePath}");
        }
        catch (Exception e)
        {
            Debug.LogError($"[FPV] BeginRecord failed: {e}");
            recordToFile = false;
        }
    }
    private void WriteFrame()
    {
        if (_writer == null) return;
        try
        {
            if (csvFormat)
            {
                _writer.Write($"{Time.frameCount},{Time.time:F6}");
                for (int i = 0; i < 24; i++)
                    _writer.Write($",{J24[i].x:F6},{J24[i].y:F6},{J24[i].z:F6}");
                _writer.WriteLine();
            }
            else
            {
                _writer.WriteLine($"frame={Time.frameCount}, t={Time.time:F6}");
                for (int i = 0; i < 24; i++)
                    _writer.WriteLine($"  p{i}: ({J24[i].x:F6},{J24[i].y:F6},{J24[i].z:F6})");
                _writer.WriteLine();
            }
            _writer.Flush();
        }
        catch (Exception e)
        {
            Debug.LogError($"[FPV] WriteFrame failed: {e}");
            recordToFile = false;
            try { _writer?.Flush(); _writer?.Close(); } catch { }
        }
    }
}



//using System;
//using System.IO;
//using UnityEngine;

//public class SMPLXRealtimeBinder_FPV : MonoBehaviour
//{
//    [Header("References")]
//    public PXR_BodyTrackingFeed feed;      // 24点来源
//    public RealTimeSMPLX smplx;            // 你的 SMPL-X 组件
//    public Transform smplxRoot;            // SMPL-X 根（整体位姿控制）
//    public Transform xrCamera;             // XR 相机（HMD）

//    [Header("Tracking Origin (PICO local -> Unity world)")]
//    public Transform trackingOrigin;       // 一般设为 feed.cubesRoot
//    public bool feedIsLocalSpace = true;   // feed里pos是否是local

//    [Header("XR Origin Frame (NEW)")]
//    [Tooltip("XR Origin（或 Camera Offset）的 Transform。启动时可把 trackingOrigin 对齐到它。")]
//    public Transform xrOrigin;
//    public bool alignTrackingToXROnStart = true; // 启动就把trackingOrigin对齐到xrOrigin
//    public bool trackingAlignYawOnly = true;     // 只对齐水平朝向更稳
//    public Vector3 trackingOriginPosOffset = Vector3.zero; // 对齐后附加偏移（米），例如(0,0,0)

//    [Header("Mapping (Feed -> SMPL24)")]
//    public int[] roleToSmpl24 = new int[24] {
//        0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23
//    };

//    [Header("Eye Anchor as Head + Offset (no eye bones)")]
//    public Vector3 headFallbackEyeLocal = new Vector3(0f, 0.07f, 0.10f); // 眼睛相对头骨偏移
//    public bool debugDrawEyeAnchor = false;

//    [Header("Align SMPLX to XR Camera (FPV)")]
//    public bool alignHeadToCamera = true;
//    public bool alignHeadRotation = true;
//    public bool yawOnly = true;
//    public float maxPosStep = 0.3f;   // 每帧最大位移
//    public float maxDegStep = 25f;    // 每帧最大旋转（度）
//    public bool snapCalibrateOnStart = true;
//    public KeyCode snapCalibrateHotkey = KeyCode.F8;

//    [Header("Scale Calibration (optional)")]
//    public bool autoCalibrateScale = false;
//    public float targetEyeHeight = 0f;    // 0=用相机y
//    public float scaleLerp = 0.2f;

//    [Header("First-Person Culling")]
//    public bool hideHeadForLocalCamera = true;
//    public string selfLayerName = "Self";
//    public Renderer[] headRenderers;

//    [Header("Recording (optional)")]
//    public bool recordToFile = false;
//    public string fileBaseName = "smplx_24pts";
//    public bool csvFormat = true;
//    public int writeEveryNFrames = 1;
//    private StreamWriter _writer;
//    private string _filePath;
//    private int _frameCounter;

//    // internals
//    private Vector3[] J24 = new Vector3[24];
//    private Transform headT;
//    private bool headReady;

//    void Awake()
//    {
//        if (smplxRoot == null && smplx != null) smplxRoot = smplx.transform;
//    }

//    void Start()
//    {
//        // ① 启动时：把 trackingOrigin 对齐到 XR Origin（位置+朝向）
//        if (alignTrackingToXROnStart && trackingOrigin != null && xrOrigin != null)
//            AlignTrackingOriginToXROrigin(trackingAlignYawOnly, trackingOriginPosOffset);

//        // 头骨引用
//        headT = smplx.JointPoints[(int)PositionIndex.head]?.Transform;
//        headReady = (headT != null);

//        if (hideHeadForLocalCamera) ApplySelfLayerToHead();
//        if (recordToFile) BeginRecord();

//        // ② 启动时：把 SMPLX（眼睛=头+偏移）吸到 XR 相机处
//        if (snapCalibrateOnStart) SnapCalibrateToCamera();
//    }

//    void OnDestroy()
//    {
//        try { _writer?.Flush(); _writer?.Close(); } catch { }
//    }

//    void Update()
//    {
//#if UNITY_ANDROID
//        if (feed == null || smplx == null) return;
//        var poses = feed.LatestRolePoses;
//        if (poses == null || poses.Length == 0) return;

//        if (Input.GetKeyDown(snapCalibrateHotkey)) SnapCalibrateToCamera();

//        // 1) 拉取24点（local->world 取决于 feedIsLocalSpace）
//        for (int i = 0; i < 24; i++)
//        {
//            int src = (i < roleToSmpl24.Length) ? roleToSmpl24[i] : i;
//            Vector3 pLocalOrWorld = (src >= 0 && src < poses.Length && poses[src].valid) ? poses[src].pos : Vector3.zero;
//            J24[i] = (feedIsLocalSpace && trackingOrigin != null) ? trackingOrigin.TransformPoint(pLocalOrWorld) : pLocalOrWorld;
//        }

//        // 2) 喂给 SMPL-X（世界坐标）
//        smplx.ApplySMPL24(
//            J24[0], J24[1], J24[2], J24[3],
//            J24[4], J24[5],
//            J24[6], J24[7], J24[8], J24[9],
//            J24[10], J24[11],
//            J24[12],
//            J24[13], J24[14],
//            J24[15],
//            J24[16], J24[17],
//            J24[18], J24[19],
//            J24[20], J24[21],
//            J24[22], J24[23],
//            headWorldRot: null, lWristWorldRot: null, rWristWorldRot: null
//        );

//        // 3) 眼睛吸附到相机（第一人称）
//        if (alignHeadToCamera && headReady && xrCamera != null && smplxRoot != null)
//            AlignHeadOffsetToCamera();

//        // 4) 可选：按眼高校准整体scale
//        if (autoCalibrateScale && headReady)
//            CalibrateScaleToEyeHeight();

//        // 5) 录制
//        if (recordToFile && (++_frameCounter % writeEveryNFrames == 0)) WriteFrame();

//        // debug
//        if (debugDrawEyeAnchor && headReady)
//        {
//            Vector3 eyesWorld = headT.TransformPoint(headFallbackEyeLocal);
//            Debug.DrawLine(eyesWorld, eyesWorld + headT.forward * 0.15f, Color.cyan);
//            Debug.DrawLine(eyesWorld, eyesWorld + headT.up * 0.12f, Color.green);
//            Debug.DrawLine(eyesWorld, eyesWorld + headT.right * 0.12f, Color.red);
//        }
//#endif
//    }

//    // ---------- NEW: trackingOrigin ↔ XR Origin 对齐 ----------
//    public void AlignTrackingOriginToXROrigin(bool yawOnlyAlign, Vector3 posOffset)
//    {
//        if (trackingOrigin == null || xrOrigin == null) return;

//        // 位置：直接贴到 xrOrigin（可加偏移）
//        trackingOrigin.position = xrOrigin.position + posOffset;

//        // 朝向：全朝向或仅 yaw
//        if (yawOnlyAlign)
//        {
//            Vector3 f = Vector3.ProjectOnPlane(xrOrigin.forward, Vector3.up).normalized;
//            if (f.sqrMagnitude < 1e-6f) f = Vector3.forward;
//            trackingOrigin.rotation = Quaternion.LookRotation(f, Vector3.up);
//        }
//        else
//        {
//            trackingOrigin.rotation = xrOrigin.rotation;
//        }
//    }

//    // ---------- FPV: 头+偏移 对齐相机 ----------
//    private void AlignHeadOffsetToCamera()
//    {
//        Quaternion headRot = headT.rotation;
//        Quaternion camRot = xrCamera.rotation;

//        // 先旋转
//        if (alignHeadRotation)
//        {
//            if (yawOnly)
//            {
//                Vector3 headF = Vector3.ProjectOnPlane(headRot * Vector3.forward, Vector3.up).normalized;
//                Vector3 camF = Vector3.ProjectOnPlane(camRot * Vector3.forward, Vector3.up).normalized;
//                if (headF.sqrMagnitude > 1e-6f && camF.sqrMagnitude > 1e-6f)
//                {
//                    Quaternion deltaYaw = Quaternion.FromToRotation(headF, camF);
//                    ApplyClampedRotation(deltaYaw);
//                }
//            }
//            else
//            {
//                Quaternion deltaR = camRot * Quaternion.Inverse(headRot);
//                ApplyClampedRotation(deltaR);
//            }
//        }

//        // 再平移：把“眼睛=头+偏移”吸到相机
//        Vector3 eyesWorld = headT.TransformPoint(headFallbackEyeLocal);
//        Vector3 delta = xrCamera.position - eyesWorld;
//        float step = delta.magnitude;
//        if (step > maxPosStep) delta = delta.normalized * maxPosStep;
//        smplxRoot.position += delta;
//    }

//    public void SnapCalibrateToCamera()
//    {
//        if (!headReady || xrCamera == null || smplxRoot == null) return;

//        // 旋转瞬贴
//        Quaternion headRot = headT.rotation;
//        Quaternion camRot = xrCamera.rotation;
//        if (alignHeadRotation)
//        {
//            if (yawOnly)
//            {
//                Vector3 headF = Vector3.ProjectOnPlane(headRot * Vector3.forward, Vector3.up).normalized;
//                Vector3 camF = Vector3.ProjectOnPlane(camRot * Vector3.forward, Vector3.up).normalized;
//                if (headF.sqrMagnitude > 1e-6f && camF.sqrMagnitude > 1e-6f)
//                {
//                    Quaternion deltaYaw = Quaternion.FromToRotation(headF, camF);
//                    smplxRoot.rotation = deltaYaw * smplxRoot.rotation;
//                }
//            }
//            else
//            {
//                Quaternion deltaR = camRot * Quaternion.Inverse(headRot);
//                smplxRoot.rotation = deltaR * smplxRoot.rotation;
//            }
//        }

//        // 位置瞬贴
//        Vector3 eyesWorld = headT.TransformPoint(headFallbackEyeLocal);
//        Vector3 delta = xrCamera.position - eyesWorld;
//        smplxRoot.position += delta;
//    }

//    private void ApplyClampedRotation(Quaternion deltaR)
//    {
//        deltaR.ToAngleAxis(out float angle, out Vector3 axis);
//        angle = Mathf.DeltaAngle(0f, angle);
//        float clamped = Mathf.Clamp(angle, -maxDegStep, maxDegStep);
//        Quaternion stepR = Quaternion.AngleAxis(clamped, axis);
//        smplxRoot.rotation = stepR * smplxRoot.rotation;
//    }

//    // ---------- Scale ----------
//    private void CalibrateScaleToEyeHeight()
//    {
//        float targetY = (targetEyeHeight > 0f) ? targetEyeHeight : xrCamera.position.y;
//        float modelEyeY = headT.TransformPoint(headFallbackEyeLocal).y;

//        float s = smplxRoot.localScale.x;
//        if (Mathf.Abs(modelEyeY) < 1e-4f || s <= 0f) return;

//        float desiredS = s * (targetY / modelEyeY);
//        float newS = Mathf.Lerp(s, desiredS, scaleLerp);
//        smplxRoot.localScale = new Vector3(newS, newS, newS);
//    }

//    // ---------- First-person culling ----------
//    private void ApplySelfLayerToHead()
//    {
//        int selfLayer = LayerMask.NameToLayer(selfLayerName);
//        if (selfLayer < 0)
//        {
//            Debug.LogWarning($"[FPV] Layer '{selfLayerName}' not found. Create it and exclude from XR Camera.");
//            return;
//        }
//        foreach (var r in headRenderers)
//        {
//            if (r == null) continue;
//            SetLayerRecursively(r.gameObject, selfLayer);
//        }
//        // 确保 XR Camera 的 Culling Mask 取消勾选 Self 层
//    }
//    private void SetLayerRecursively(GameObject go, int layer)
//    {
//        go.layer = layer;
//        foreach (Transform c in go.transform) SetLayerRecursively(c.gameObject, layer);
//    }

//    // ---------- Recording ----------
//    private void BeginRecord()
//    {
//        try
//        {
//            string ts = DateTime.Now.ToString("yyyyMMdd_HHmmss");
//            string ext = csvFormat ? "csv" : "txt";
//            _filePath = Path.Combine(Application.persistentDataPath, $"{fileBaseName}_{ts}.{ext}");
//            _writer = new StreamWriter(_filePath, false, System.Text.Encoding.UTF8);

//            if (csvFormat)
//            {
//                _writer.Write("frame,time");
//                for (int i = 0; i < 24; i++) _writer.Write($",p{i}x,p{i}y,p{i}z");
//                _writer.WriteLine();
//            }
//            else
//            {
//                _writer.WriteLine("# smplx 24 points stream");
//            }
//            _writer.Flush();
//            Debug.Log($"[FPV] Recording to: {_filePath}");
//        }
//        catch (Exception e)
//        {
//            Debug.LogError($"[FPV] BeginRecord failed: {e}");
//            recordToFile = false;
//        }
//    }
//    private void WriteFrame()
//    {
//        if (_writer == null) return;
//        try
//        {
//            if (csvFormat)
//            {
//                _writer.Write($"{Time.frameCount},{Time.time:F6}");
//                for (int i = 0; i < 24; i++)
//                    _writer.Write($",{J24[i].x:F6},{J24[i].y:F6},{J24[i].z:F6}");
//                _writer.WriteLine();
//            }
//            else
//            {
//                _writer.WriteLine($"frame={Time.frameCount}, t={Time.time:F6}");
//                for (int i = 0; i < 24; i++)
//                    _writer.WriteLine($"  p{i}: ({J24[i].x:F6},{J24[i].y:F6},{J24[i].z:F6})");
//                _writer.WriteLine();
//            }
//            _writer.Flush();
//        }
//        catch (Exception e)
//        {
//            Debug.LogError($"[FPV] WriteFrame failed: {e}");
//            recordToFile = false;
//            try { _writer?.Flush(); _writer?.Close(); } catch { }
//        }
//    }
//}

