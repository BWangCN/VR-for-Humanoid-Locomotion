using System;
using UnityEngine;
#if UNITY_EDITOR
using UnityEditor;
#endif
#if UNITY_ANDROID
using Unity.XR.PXR;
#endif

public class PXRBodyTrackingMonitor : MonoBehaviour
{
    [Header("Inputs")]
    public PXR_BodyTrackingBlock pico;       // 拖你的 PXR_BodyTrackingBlock
    public Transform trackingOrigin;         // XR Origin/CameraRig（用于 local->world，可为空）
    public float gizmoSize = 0.05f;

    [Header("Colors")]
    public Color validColor = Color.green;
    public Color invalidColor = Color.gray;
    public Color textColor = Color.white;

    [Header("HUD")]
    public bool showHUD = true;
    public bool showInEditorPlayMode = true; // 在 Editor/Play 下也显示（仅可视化，不取 PICO SDK 状态）

    // 内部状态
    private Vector3[] _prevLocalPos = new Vector3[24];
    private bool _hasPrev = false;
    private int _validCount = 0;
    private int _changedThisSecond = 0;
    private float _secTimer = 0f;
    private float _fps = 0f;
    private float _lastDataTime = -1f;
    private string _sdkState = "Unknown";
    private string _sdkMsg = "-";
    private bool _supported = false;
    private bool _trackingValid = false;

    void Update()
    {
        if (pico == null || pico.LatestRolePoses == null || pico.LatestRolePoses.Length < 24)
            return;

        // 统计有效点与是否发生变化（位置变化阈值）
        const float changeEps = 1e-5f;
        _validCount = 0;
        bool anyChanged = false;
        for (int i = 0; i < 24; i++)
        {
            var rp = pico.LatestRolePoses[i];
            if (rp.valid) _validCount++;

            if (_hasPrev)
            {
                if ((rp.localPos - _prevLocalPos[i]).sqrMagnitude > changeEps)
                    anyChanged = true;
            }
            _prevLocalPos[i] = rp.localPos;
        }

        if (!_hasPrev) _hasPrev = true;

        // 有任一关节变化，认为这帧“有数据更新”
        if (anyChanged)
        {
            _changedThisSecond++;
            _lastDataTime = Time.time;
        }

        // 每秒更新一次伪 FPS（基于“有变化的帧数”）
        _secTimer += Time.deltaTime;
        if (_secTimer >= 1f)
        {
            _fps = _changedThisSecond / _secTimer;
            _changedThisSecond = 0;
            _secTimer = 0f;
        }

        // 查询 SDK 状态（仅在 Android 设备上）
#if UNITY_ANDROID
        bool istracking = false;
        BodyTrackingStatus bs = new BodyTrackingStatus();
        PXR_MotionTracking.GetBodyTrackingSupported(ref _supported);
        PXR_MotionTracking.GetBodyTrackingState(ref istracking, ref bs);
        _trackingValid = (bs.stateCode == BodyTrackingStatusCode.BT_VALID);
        _sdkState = bs.stateCode.ToString();
        _sdkMsg = bs.message.ToString();
#else
        // Editor/PC 下给出提示
        _supported = false;
        _trackingValid = false;
        _sdkState = Application.isEditor ? "Editor (no Android runtime)" : "Non-Android";
        _sdkMsg = "-";
#endif
    }

    void OnDrawGizmos()
    {
        // 在 Scene 里画 24 个点（即使暂停也能看）
        if (!Application.isPlaying && !showInEditorPlayMode) return;
        if (pico == null || pico.LatestRolePoses == null || pico.LatestRolePoses.Length < 24) return;

        for (int i = 0; i < 24; i++)
        {
            var rp = pico.LatestRolePoses[i];

            Vector3 wPos;
            if (trackingOrigin != null)
                wPos = trackingOrigin.TransformPoint(rp.localPos);
            else
                wPos = rp.localPos;

            Gizmos.color = rp.valid ? validColor : invalidColor;
            Gizmos.DrawSphere(wPos, gizmoSize);
        }
    }

#if UNITY_EDITOR
    void OnDrawGizmosSelected()
    {
        // 选中时可在 Scene 里标注索引
        if (!Application.isPlaying && !showInEditorPlayMode) return;
        if (pico == null || pico.LatestRolePoses == null || pico.LatestRolePoses.Length < 24) return;

        Handles.color = Color.yellow;
        var style = new GUIStyle(EditorStyles.boldLabel);
        style.normal.textColor = Color.yellow;
        for (int i = 0; i < 24; i++)
        {
            var rp = pico.LatestRolePoses[i];
            Vector3 wPos = trackingOrigin ? trackingOrigin.TransformPoint(rp.localPos) : rp.localPos;
            Handles.Label(wPos + Vector3.up * (gizmoSize * 1.5f), $"#{i}", style);
        }
    }
#endif

    void OnGUI()
    {
        if (!showHUD) return;
        if (!Application.isPlaying && !showInEditorPlayMode) return;

        var oldColor = GUI.color;
        GUI.color = textColor;

        GUILayout.BeginArea(new Rect(12, 12, 520, 200), GUI.skin.box);
        GUILayout.Label("<b><size=14>PICO Body Tracking Monitor</size></b>");
        GUILayout.Space(4);

        GUILayout.Label($"Supported: {_supported}");
        GUILayout.Label($"SDK State: {_sdkState}");
        GUILayout.Label($"SDK Msg  : {_sdkMsg}");
        GUILayout.Label($"Valid Joints: {_validCount}/24");

        string uptime = _lastDataTime < 0 ? "Never" : $"{(Time.time - _lastDataTime):0.00}s ago";
        GUILayout.Label($"Last Data Update: {uptime}");

        GUILayout.Label($"Update FPS (pos changed frames/sec): {_fps:0.0}");

#if !UNITY_ANDROID
        GUILayout.Space(6);
        GUILayout.Label("<color=orange>Note: Running outside Android. SDK state may be placeholder. " +
                        "Joint spheres still reflect LatestRolePoses.</color>");
#endif
        GUILayout.EndArea();

        GUI.color = oldColor;
    }
}

