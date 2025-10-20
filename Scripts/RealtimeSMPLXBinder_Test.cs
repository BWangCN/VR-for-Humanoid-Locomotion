using System;
using System.IO;
using UnityEngine;
using UnityEngine.Events;

/// <summary>
/// 仅做 24 点的坐标转换与可选适配：
/// - 从 PXR_BodyTrackingFeed 取 24 点
/// - 可将 PICO local 转为 Unity world（trackingOrigin.TransformPoint）
/// - 可做 yaw 偏置 / 轴翻转 / XZ 交换 / 左右对调
/// - 输出 WorldJoints24（Vector3[24]）
/// - 可视化与可选 CSV 记录
/// 不涉及 SMPLX 驱动。
/// </summary>
public class PXR_24PointMapper : MonoBehaviour
{
    [Header("Source")]
    public PXR_BodyTrackingFeed feed;

    [Tooltip("feed.LatestRolePoses 是否是 PICO local 坐标。如果是，则使用 trackingOrigin.TransformPoint 转世界。")]
    public bool sourceIsLocalSpace = true;

    [Tooltip("PICO 追踪坐标系在 Unity 中的参考根，通常等于 feed.cubesRoot（或你自己的 TrackingOrigin）。")]
    public Transform trackingOrigin;

    [Header("Pose Adapter (optional)")]
    [Tooltip("围绕 Y 轴的全局偏置（度）。常用 180 可解决前后反。")]
    public float yawOffsetDeg = 0f;
    public bool flipX = false;
    public bool flipY = false;
    public bool flipZ = false;     // 膝/肘反向常见修正：先试 true
    public bool swapXZ = false;    // 坐标系 Z-up 等情形可尝试
    public bool swapLeftRight = false;

    [Header("Gizmos")]
    public bool drawGizmos = true;
    public float gizmoPointSize = 0.025f;
    public Color gizmoPointColor = new Color(0.1f, 0.8f, 1f, 1f);
    public Color gizmoBoneColor = new Color(1f, 0.9f, 0.2f, 1f);

    [Header("Recording (optional)")]
    public bool recordCsv = false;
    public string fileBaseName = "pxr_24pts_world";
    public int writeEveryNFrames = 1;

    [Serializable] public class PointsUpdatedEvent : UnityEvent<Vector3[]> { }
    [Header("Events")]
    public PointsUpdatedEvent OnPointsUpdated;

    // 输出：世界坐标 24 点
    public Vector3[] WorldJoints24 { get; private set; } = new Vector3[24];

    private StreamWriter _writer;
    private int _frameCounter;
    private string _filePath;

    // 左右对调的索引（按你的 PositionIndex 定义）
    private static readonly int[] L = {
        (int)PositionIndex.left_hip, (int)PositionIndex.left_knee, (int)PositionIndex.left_ankle,
        (int)PositionIndex.left_foot_index, (int)PositionIndex.left_collar, (int)PositionIndex.left_shoulder,
        (int)PositionIndex.left_elbow, (int)PositionIndex.left_wrist, (int)PositionIndex.left_hand
    };
    private static readonly int[] R = {
        (int)PositionIndex.right_hip, (int)PositionIndex.right_knee, (int)PositionIndex.right_ankle,
        (int)PositionIndex.right_foot_index, (int)PositionIndex.right_collar, (int)PositionIndex.right_shoulder,
        (int)PositionIndex.right_elbow, (int)PositionIndex.right_wrist, (int)PositionIndex.right_hand
    };

    void Start()
    {
        if (recordCsv)
        {
            try
            {
                string ts = DateTime.Now.ToString("yyyyMMdd_HHmmss");
                _filePath = Path.Combine(Application.persistentDataPath, $"{fileBaseName}_{ts}.csv");
                _writer = new StreamWriter(_filePath, false, System.Text.Encoding.UTF8);
                // header
                _writer.Write("frame,time");
                for (int i = 0; i < 24; i++) _writer.Write($",p{i}x,p{i}y,p{i}z");
                _writer.WriteLine();
                _writer.Flush();
                Debug.Log($"[24PointMapper] Recording to {_filePath}");
            }
            catch (Exception e)
            {
                Debug.LogError($"[24PointMapper] Cannot open writer: {e}");
                recordCsv = false;
            }
        }
    }

    void OnDestroy()
    {
        try { _writer?.Flush(); _writer?.Close(); } catch { }
    }

    void Update()
    {
#if UNITY_ANDROID
        if (feed == null) return;
        var poses = feed.LatestRolePoses;
        if (poses == null || poses.Length < 24) return;

        // 1) 取 24 点并转世界
        for (int i = 0; i < 24; i++)
        {
            Vector3 p = poses[i].valid ? poses[i].pos : Vector3.zero;

            // local -> world
            if (sourceIsLocalSpace && trackingOrigin != null)
                p = trackingOrigin.TransformPoint(p);

            // 姿态适配
            p = AdaptPoint(p);

            WorldJoints24[i] = p;
        }

        // 2) 左右对调（可选）
        if (swapLeftRight)
        {
            for (int i = 0; i < L.Length; i++)
            {
                int li = L[i], ri = R[i];
                (WorldJoints24[li], WorldJoints24[ri]) = (WorldJoints24[ri], WorldJoints24[li]);
            }
        }

        // 3) 事件回调
        OnPointsUpdated?.Invoke(WorldJoints24);

        // 4) 记录
        if (recordCsv && (++_frameCounter % writeEveryNFrames == 0))
            WriteCsv(WorldJoints24);
#endif
    }

    // ----------------- 辅助：姿态适配 -----------------
    private Vector3 AdaptPoint(Vector3 pWorld)
    {
        // 在 trackingOrigin 局部空间做轴操作会更直观
        Transform T = trackingOrigin != null ? trackingOrigin : null;
        Vector3 local = T ? T.InverseTransformPoint(pWorld) : pWorld;

        // 轴交换
        if (swapXZ) local = new Vector3(local.z, local.y, local.x);

        // 轴取反
        local = new Vector3(
            flipX ? -local.x : local.x,
            flipY ? -local.y : local.y,
            flipZ ? -local.z : local.z
        );

        // 回世界
        Vector3 w = T ? T.TransformPoint(local) : local;

        // 全局 yaw 偏置（围绕 Y）
        if (Mathf.Abs(yawOffsetDeg) > 0.001f)
        {
            Quaternion yaw = Quaternion.AngleAxis(yawOffsetDeg, Vector3.up);
            if (T)
            {
                Vector3 rel = w - T.position;
                w = T.position + yaw * rel;
            }
            else
            {
                w = yaw * w;
            }
        }
        return w;
    }

    private void WriteCsv(Vector3[] pts)
    {
        if (_writer == null) return;
        try
        {
            _writer.Write($"{Time.frameCount},{Time.time:F6}");
            for (int i = 0; i < 24; i++)
                _writer.Write($",{pts[i].x:F6},{pts[i].y:F6},{pts[i].z:F6}");
            _writer.WriteLine();
            _writer.Flush();
        }
        catch (Exception e)
        {
            Debug.LogError($"[24PointMapper] WriteCsv failed: {e}");
            recordCsv = false;
            try { _writer?.Flush(); _writer?.Close(); } catch { }
        }
    }

    // ----------------- Gizmos 可视化 -----------------
    void OnDrawGizmos()
    {
        if (!drawGizmos || WorldJoints24 == null || WorldJoints24.Length < 24) return;

        // 点
        Gizmos.color = gizmoPointColor;
        foreach (var p in WorldJoints24)
            Gizmos.DrawSphere(p, gizmoPointSize);

        // 简单骨连接（可按需扩展/调整）
        Gizmos.color = gizmoBoneColor;
        DrawBone(PositionIndex.hip, PositionIndex.spine1);
        DrawBone(PositionIndex.spine1, PositionIndex.spine2);
        DrawBone(PositionIndex.spine2, PositionIndex.spine3);
        DrawBone(PositionIndex.spine3, PositionIndex.neck);
        DrawBone(PositionIndex.neck, PositionIndex.head);

        DrawBone(PositionIndex.left_hip, PositionIndex.left_knee);
        DrawBone(PositionIndex.left_knee, PositionIndex.left_ankle);
        DrawBone(PositionIndex.left_ankle, PositionIndex.left_foot_index);

        DrawBone(PositionIndex.right_hip, PositionIndex.right_knee);
        DrawBone(PositionIndex.right_knee, PositionIndex.right_ankle);
        DrawBone(PositionIndex.right_ankle, PositionIndex.right_foot_index);

        DrawBone(PositionIndex.left_collar, PositionIndex.left_shoulder);
        DrawBone(PositionIndex.left_shoulder, PositionIndex.left_elbow);
        DrawBone(PositionIndex.left_elbow, PositionIndex.left_wrist);
        DrawBone(PositionIndex.left_wrist, PositionIndex.left_hand);

        DrawBone(PositionIndex.right_collar, PositionIndex.right_shoulder);
        DrawBone(PositionIndex.right_shoulder, PositionIndex.right_elbow);
        DrawBone(PositionIndex.right_elbow, PositionIndex.right_wrist);
        DrawBone(PositionIndex.right_wrist, PositionIndex.right_hand);
    }

    private void DrawBone(PositionIndex a, PositionIndex b)
    {
        int ia = (int)a, ib = (int)b;
        if (WorldJoints24 == null || ia >= WorldJoints24.Length || ib >= WorldJoints24.Length) return;
        Vector3 pa = WorldJoints24[ia], pb = WorldJoints24[ib];
        Gizmos.DrawLine(pa, pb);
    }
}

