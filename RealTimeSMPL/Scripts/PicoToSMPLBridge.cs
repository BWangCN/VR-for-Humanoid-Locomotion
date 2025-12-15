using UnityEngine;

/// <summary>
/// 一个极简桥接：把“外部（你当前非过时PICO API）采集的24关节 + 可选头/手腕旋转”
/// 按 RealTimeSMPLX.ApplySMPL24 的参数顺序送进去。
/// 不包含任何对PICO API的直接调用——你现有读取代码每帧调用 PushFrame 即可。
/// </summary>
public class PicoToSMPLXBridge : MonoBehaviour
{
    [Header("Targets")]
    public RealTimeSMPLX smplx;          // 指向你的 RealTimeSMPLX 组件（通常挂在SMPLX模型上）
    public Transform trackingOrigin;     // 采集到的关节是“追踪空间”时，指定其原点（XR Rig根等）。留空则认为已是世界坐标

    [Header("Space & Scale")]
    public float scaleMetersToModel = 1.0f;  // PICO单位是米；若你的Avatar缩放不同，这里统一缩放
    public Vector3 extraOffset = Vector3.zero; // 额外平移（可不设）

    [Header("Optional Remap (PICO顺序 -> 本类顺序)")]
    // 若你的PICO关节顺序与下面 PositionIndex 一致，保持默认身份映射即可。
    // 如不一致，填成“我方索引 i 对应 PICO的第 remap[i] 个”。
    public int[] remap = new int[24]
    {
        0, 1, 2, 3,   // pelvis, l_hip, r_hip, spine1
        4, 5,         // l_knee, r_knee
        6, 7, 8, 9,   // spine2, l_ankle, r_ankle, spine3
        10, 11,       // l_foot, r_foot
        12,           // neck
        13, 14,       // left_collar, right_collar
        15,           // head
        16, 17,       // left_shoulder, right_shoulder
        18, 19,       // left_elbow, right_elbow
        20, 21,       // left_wrist, right_wrist
        22, 23        // left_hand, right_hand
    };

    /// <summary>
    /// 由你当前的（非过时）PICO读取代码在 Update/OnBeforeRender 中调用：
    /// joints24：长度>=24，单位米，按 PICO 的顺序（或与 remap 搭配后能映射到下列PositionIndex）
    /// headWorldRot / lWristWorldRot / rWristWorldRot：可选世界旋转（若你能从PICO直接拿到）
    /// </summary>
    public void PushFrame(
        Vector3[] joints24,
        Quaternion? headWorldRot = null,
        Quaternion? lWristWorldRot = null,
        Quaternion? rWristWorldRot = null)
    {
        if (smplx == null || joints24 == null || joints24.Length < 24) return;

        // 1) 追踪空间 → 世界空间（或保持世界）
        //    - 如提供 trackingOrigin，认为 joints24 是“trackingOrigin局部”；否则认为已是世界坐标
        //    - 再乘以统一尺度 + 偏移
        Vector3[] W = new Vector3[24];
        for (int i = 0; i < 24; i++)
        {
            int src = (remap != null && remap.Length >= 24) ? remap[i] : i;
            Vector3 p = joints24[src] * scaleMetersToModel;
            if (trackingOrigin != null) p = trackingOrigin.TransformPoint(p);
            p += extraOffset;
            W[i] = p;
        }

        // 2) 按 RealTimeSMPLX.PositionIndex 的定义顺序喂给 ApplySMPL24
        //    （顺序与你给出的 PositionIndex 完全一致）
        if (headWorldRot.HasValue || lWristWorldRot.HasValue || rWristWorldRot.HasValue)
        {
            smplx.ApplySMPL24(
                // 0–3 pelvis + spine
                W[(int)PositionIndex.hip],
                W[(int)PositionIndex.left_hip],
                W[(int)PositionIndex.right_hip],
                W[(int)PositionIndex.spine1],

                // 4–5 knees
                W[(int)PositionIndex.left_knee],
                W[(int)PositionIndex.right_knee],

                // 6–9 ankles + spine2/3
                W[(int)PositionIndex.spine2],
                W[(int)PositionIndex.left_ankle],
                W[(int)PositionIndex.right_ankle],
                W[(int)PositionIndex.spine3],

                // 10–11 feet
                W[(int)PositionIndex.left_foot_index],
                W[(int)PositionIndex.right_foot_index],

                // 12 neck
                W[(int)PositionIndex.neck],

                // 13–14 collars
                W[(int)PositionIndex.left_collar],
                W[(int)PositionIndex.right_collar],

                // 15 head
                W[(int)PositionIndex.head],

                // 16–17 shoulders
                W[(int)PositionIndex.left_shoulder],
                W[(int)PositionIndex.right_shoulder],

                // 18–19 elbows
                W[(int)PositionIndex.left_elbow],
                W[(int)PositionIndex.right_elbow],

                // 20–21 wrists
                W[(int)PositionIndex.left_wrist],
                W[(int)PositionIndex.right_wrist],

                // 22–23 hands
                W[(int)PositionIndex.left_hand],
                W[(int)PositionIndex.right_hand],

                // 可选世界旋转
                headWorldRot, lWristWorldRot, rWristWorldRot
            );
        }
        else
        {
            smplx.ApplySMPL24(
                W[(int)PositionIndex.hip],
                W[(int)PositionIndex.left_hip],
                W[(int)PositionIndex.right_hip],
                W[(int)PositionIndex.spine1],
                W[(int)PositionIndex.left_knee],
                W[(int)PositionIndex.right_knee],
                W[(int)PositionIndex.spine2],
                W[(int)PositionIndex.left_ankle],
                W[(int)PositionIndex.right_ankle],
                W[(int)PositionIndex.spine3],
                W[(int)PositionIndex.left_foot_index],
                W[(int)PositionIndex.right_foot_index],
                W[(int)PositionIndex.neck],
                W[(int)PositionIndex.left_collar],
                W[(int)PositionIndex.right_collar],
                W[(int)PositionIndex.head],
                W[(int)PositionIndex.left_shoulder],
                W[(int)PositionIndex.right_shoulder],
                W[(int)PositionIndex.left_elbow],
                W[(int)PositionIndex.right_elbow],
                W[(int)PositionIndex.left_wrist],
                W[(int)PositionIndex.right_wrist],
                W[(int)PositionIndex.left_hand],
                W[(int)PositionIndex.right_hand]
            );
        }
    }
}
