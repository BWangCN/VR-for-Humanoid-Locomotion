using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using UnityEngine;

/// <summary>
/// 从 PXR_24PointMapper 生成的 CSV 中读取 24 关节的 world position，
/// 在同一场景中以火柴人形式回放。
///
/// CSV 格式假定为：
/// frame,time,
///   p0x,p0y,p0z,q0w,q0x,q0y,q0z,
///   ...
///   p23x,p23y,p23z,q23w,q23x,q23y,q23z
///
/// 这里只使用 p{i}x/p{i}y/p{i}z 来可视化，忽略四元数。
/// </summary>
public class PXR_24PointCsvPlayer : MonoBehaviour
{
    [Header("CSV 设置")]
    [Tooltip("CSV 文件名（不含路径），默认从 Application.persistentDataPath 下读取。")]
    public string csvFileName = "C:\\Users\\Wayne\\Desktop\\GMU\\PhD\\Humanoid\\smpl_24pts_test_20251114_133818.csv";

    [Tooltip("是否在 Start 时自动加载 CSV")]
    public bool loadOnStart = true;

    [Header("播放设置")]
    [Tooltip("回放时使用的帧率（逻辑播放用，不一定等于录制时的帧率）")]
    public float playbackFps = 60f;

    [Tooltip("播到最后一帧是否循环回到开头")]
    public bool loop = true;

    [Tooltip("开始时是否自动播放")]
    public bool autoPlay = true;

    [Header("Gizmos 可视化")]
    public bool drawGizmos = true;
    public float gizmoPointSize = 0.025f;
    public Color gizmoPointColor = new Color(0.1f, 0.8f, 1f, 1f);
    public Color gizmoBoneColor = new Color(1f, 0.9f, 0.2f, 1f);

    // 存储从 CSV 读取的所有帧，每帧是 24 个点的数组
    private List<Vector3[]> _frames = new List<Vector3[]>();
    private int _totalFrames = 0;

    // 当前播放用的一帧 24 点（用于 Gizmos）
    private Vector3[] _currentJoints24 = new Vector3[24];

    // 播放时间累积（秒）
    private float _timeCursor = 0f;
    private bool _isPlaying = false;

    void Start()
    {
        if (loadOnStart)
        {
            LoadCsv();
        }

        _isPlaying = autoPlay;
    }

    void Update()
    {
        if (!_isPlaying || _totalFrames == 0) return;

        // 根据 playbackFps 推进时间，计算当前应显示哪一帧
        _timeCursor += Time.deltaTime;
        float frameFloat = _timeCursor * playbackFps;
        int frameIndex = Mathf.FloorToInt(frameFloat);

        if (frameIndex >= _totalFrames)
        {
            if (loop)
            {
                frameIndex = frameIndex % _totalFrames;
                _timeCursor = frameIndex / playbackFps;
            }
            else
            {
                frameIndex = _totalFrames - 1;
                _isPlaying = false;  // 播到最后一帧停住
            }
        }

        // 更新当前 24 点
        Array.Copy(_frames[frameIndex], _currentJoints24, 24);
    }

    /// <summary>
    /// 从 CSV 文件读取所有帧。
    /// 默认路径：Application.persistentDataPath/csvFileName
    /// </summary>
    public void LoadCsv()
    {
        _frames.Clear();
        _totalFrames = 0;
        _timeCursor = 0f;

        //string path = Path.Combine(Application.persistentDataPath, csvFileName);
        string path = csvFileName;

        // 如果不是绝对路径，就自动拼到 persistentDataPath
        if (!Path.IsPathRooted(csvFileName))
        {
            path = Path.Combine(Application.persistentDataPath, csvFileName);
        }

        if (!File.Exists(path))
        {
            Debug.LogError($"[PXR_24PointCsvPlayer] CSV not found: {path}");
            return;
        }


        if (!File.Exists(path))
        {
            Debug.LogError($"[PXR_24PointCsvPlayer] CSV not found: {path}");
            return;
        }

        Debug.Log($"[PXR_24PointCsvPlayer] Loading CSV: {path}");

        try
        {
            var lines = File.ReadAllLines(path);
            if (lines.Length <= 1)
            {
                Debug.LogError("[PXR_24PointCsvPlayer] CSV has no data lines.");
                return;
            }

            // 跳过第一行 header，从第二行开始解析
            for (int lineIndex = 1; lineIndex < lines.Length; lineIndex++)
            {
                string line = lines[lineIndex].Trim();
                if (string.IsNullOrEmpty(line))
                    continue;

                string[] cols = line.Split(',');
                // 最少应有：frame,time + 24*(7) = 2 + 168 = 170 列
                if (cols.Length < 170)
                {
                    Debug.LogWarning($"[PXR_24PointCsvPlayer] Line {lineIndex + 1} has too few columns ({cols.Length}). Skipped.");
                    continue;
                }

                Vector3[] joints = new Vector3[24];

                // 从第 2 列开始（index 0=frame, 1=time）
                // 每个关节占 7 列：px,py,pz,qw,qx,qy,qz
                // 我们只读取前 3 列位置
                int baseCol = 2;
                for (int j = 0; j < 24; j++)
                {
                    int idx = baseCol + j * 7;

                    float px = ParseFloat(cols[idx + 0]);
                    float py = ParseFloat(cols[idx + 1]);
                    float pz = ParseFloat(cols[idx + 2]);

                    joints[j] = new Vector3(px, py, pz);
                }

                _frames.Add(joints);
            }

            _totalFrames = _frames.Count;
            if (_totalFrames > 0)
            {
                // 初始化当前一帧为第一帧
                Array.Copy(_frames[0], _currentJoints24, 24);
                Debug.Log($"[PXR_24PointCsvPlayer] Loaded {_totalFrames} frames.");
            }
            else
            {
                Debug.LogWarning("[PXR_24PointCsvPlayer] No valid frames parsed from CSV.");
            }
        }
        catch (Exception e)
        {
            Debug.LogError($"[PXR_24PointCsvPlayer] LoadCsv failed: {e}");
        }
    }

    private float ParseFloat(string s)
    {
        if (float.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out float v))
            return v;
        return 0f;
    }

    // ----------------- 播放控制（可在 Inspector 或其他脚本调用） -----------------
    public void Play()
    {
        if (_totalFrames == 0) return;
        _isPlaying = true;
    }

    public void Pause()
    {
        _isPlaying = false;
    }

    public void Stop()
    {
        _isPlaying = false;
        _timeCursor = 0f;
        if (_totalFrames > 0)
            Array.Copy(_frames[0], _currentJoints24, 24);
    }

    // ----------------- Gizmos 可视化 -----------------
    void OnDrawGizmos()
    {
        if (!drawGizmos || _currentJoints24 == null || _currentJoints24.Length < 24)
            return;

        // 画点
        Gizmos.color = gizmoPointColor;
        foreach (var p in _currentJoints24)
            Gizmos.DrawSphere(p, gizmoPointSize);

        // 画骨骼
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
        if (_currentJoints24 == null || ia >= _currentJoints24.Length || ib >= _currentJoints24.Length)
            return;

        Vector3 pa = _currentJoints24[ia];
        Vector3 pb = _currentJoints24[ib];
        Gizmos.DrawLine(pa, pb);
    }
}
