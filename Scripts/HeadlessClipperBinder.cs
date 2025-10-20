using UnityEngine;
using Unity.XR.PXR; // 需要 PICO 的枚举 BodyTrackerRole

[RequireComponent(typeof(SkinnedMeshRenderer))]
public class HeadlessClipBinder : MonoBehaviour
{
    [Header("Sources")]
    public PXR_BodyTrackingFeed feed;   // 你的 PICO 24点输入
    public Transform trackingOrigin;    // feed 的局部 -> 世界 参考（一般等于 feed.cubesRoot）
    public bool feedIsLocalSpace = true;

    [Header("Joint Indices (PICO)")]
    public BodyTrackerRole headRole = BodyTrackerRole.HEAD;
    public BodyTrackerRole neckRole = BodyTrackerRole.NECK;
    public BodyTrackerRole lShoulderRole = BodyTrackerRole.LEFT_SHOULDER;
    public BodyTrackerRole rShoulderRole = BodyTrackerRole.RIGHT_SHOULDER;

    [Header("Clip Settings")]
    public bool enableClip = true;          // 第一人称开关
    [Tooltip("把平面往头部方向推一点，避免切到脖子皮肤")]
    public float clipBias = 0.02f;          // 对应 _ClipBias（米）
    [Tooltip("若头/颈缺失，用这个方向做兜底")]
    public Vector3 fallbackNormalWorld = Vector3.up;
    [Range(0f, 1f)] public float smooth = 0.15f; // 平滑系数，0=生硬，1=超平滑

    [Header("Only When Near Camera (optional)")]
    public Transform xrCamera;              // 若只想在本地相机附近启用
    public bool onlyEnableNearCamera = false;
    public float enableRadius = 0.25f;

    // internals
    SkinnedMeshRenderer smr;
    MaterialPropertyBlock mpb;
    static readonly int _ClipEnableID = Shader.PropertyToID("_ClipEnable");
    static readonly int _ClipOriginID = Shader.PropertyToID("_ClipOrigin");
    static readonly int _ClipNormalID = Shader.PropertyToID("_ClipNormal");
    static readonly int _ClipBiasID = Shader.PropertyToID("_ClipBias");

    Vector3 originSmoothed;
    Vector3 normalSmoothed;
    bool hasInit;

    void Awake()
    {
        smr = GetComponent<SkinnedMeshRenderer>();
        mpb = new MaterialPropertyBlock();
    }

    void LateUpdate()
    {
#if UNITY_ANDROID
        if (feed == null || feed.LatestRolePoses == null) return;

        // 动态启用条件（可选）
        bool doClip = enableClip;
        if (onlyEnableNearCamera && xrCamera != null)
        {
            // 取颈部或躯干附近点与相机的距离
            Vector3 refP;

            bool vN;
            Vector3 neckPos = TryGetWorld(neckRole, out vN);

            if (vN)
            {
                refP = neckPos;
            }
            else
            {
                bool vH;
                Vector3 headPos = TryGetWorld(headRole, out vH);
                if (vH)
                    refP = headPos;
                else
                    refP = transform.position;  // 兜底
            }

            float d = Vector3.Distance(refP, xrCamera.position);
            doClip &= (d <= enableRadius);

        }

        // 计算裁剪平面（世界空间）
        Vector3 originW, normalW;
        ComputeClipPlane(out originW, out normalW);

        // 平滑
        if (!hasInit)
        {
            originSmoothed = originW;
            normalSmoothed = normalW;
            hasInit = true;
        }
        else
        {
            originSmoothed = Vector3.Lerp(originSmoothed, originW, 1f - smooth);
            normalSmoothed = Vector3.Slerp(normalSmoothed, normalW, 1f - smooth);
        }

        // 写入 MPB（不脏材质实例）
        smr.GetPropertyBlock(mpb);
        mpb.SetFloat(_ClipEnableID, doClip ? 1f : 0f);
        mpb.SetVector(_ClipOriginID, originSmoothed);
        mpb.SetVector(_ClipNormalID, normalSmoothed.normalized);
        mpb.SetFloat(_ClipBiasID, clipBias);
        smr.SetPropertyBlock(mpb);
#endif
    }

    // —— 从 feed 里拿一个关节的世界坐标（自动处理 local->world）——
    Vector3 TryGetWorld(BodyTrackerRole role, out bool valid)
    {
        int idx = (int)role;
        valid = false;
        if (feed.LatestRolePoses == null || idx < 0 || idx >= feed.LatestRolePoses.Length) return Vector3.zero;
        var rp = feed.LatestRolePoses[idx];
        if (!rp.valid) return Vector3.zero;

        valid = true;
        if (feedIsLocalSpace && trackingOrigin != null)
            return trackingOrigin.TransformPoint(rp.pos);
        else
            return rp.pos; // 已是世界坐标（或仅调试）
    }

    // —— 规则：优先用 head-neck 方向；缺一则用肩线估计；再缺则 fallback —— 
    void ComputeClipPlane(out Vector3 originW, out Vector3 normalW)
    {
        bool vH, vN, vL, vR;
        Vector3 headW = TryGetWorld(headRole, out vH);
        Vector3 neckW = TryGetWorld(neckRole, out vN);
        Vector3 lSW = TryGetWorld(lShoulderRole, out vL);
        Vector3 rSW = TryGetWorld(rShoulderRole, out vR);

        if (vN && vH)
        {
            // 平面过颈部，法线指向头（最佳）
            originW = neckW;
            normalW = (headW - neckW).normalized;
            if (normalW.sqrMagnitude < 1e-6f) normalW = fallbackNormalWorld.normalized;
            return;
        }

        if (vN && vL && vR)
        {
            // 没有 head：用肩中点估一个“头方向”
            Vector3 midShoulder = 0.5f * (lSW + rSW);
            Vector3 approxHeadDir = (midShoulder - neckW); // 这通常指向胸；我们需要“向上”
            // 使用世界 Up 进行修正：投影到垂直平面里，保证大致向上
            Vector3 up = Vector3.up;
            Vector3 alongUp = Vector3.ProjectOnPlane(up, (rSW - lSW)).normalized; // 与肩线垂直且尽量向上
            if (alongUp.sqrMagnitude < 1e-6f) alongUp = up;
            originW = neckW;
            normalW = alongUp;
            return;
        }

        if (vN)
        {
            originW = neckW;
            normalW = fallbackNormalWorld.normalized;
            return;
        }

        // 兜底：用模型当前位置 + 世界Up
        originW = transform.position + Vector3.up * 1.3f;
        normalW = fallbackNormalWorld.normalized;
    }
}

