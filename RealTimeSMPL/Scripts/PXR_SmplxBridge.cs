using Unity.XR.PXR;
using UnityEngine;

public class PXR_SmplxBridgeSimple : MonoBehaviour
{
    public PXR_BodyTrackingBlock pico;   // 拖入你的 PXR_BodyTrackingBlock 实例
    public RealTimeSMPLX smplx;          // 拖入挂了 RealTimeSMPLX 的 SMPLX 角色
    public Transform trackingOrigin;     // XR Origin/CameraRig（负责 local->world）
    public float scale = 1.0f;
    public bool feedHeadAndWristsRotation = true;

    // 如 PICO 的24点顺序与 SMPLX PositionIndex 顺序不完全一致，可在此重排
    // 默认一一对应（identity）
    public int[] picoToSmplx = new int[24]{
        0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23
    };

    private void Start()
    {
        // 设置骨骼长度
        BodyTrackingBoneLength boneLength = new BodyTrackingBoneLength();
        // 开始全身动捕
        int ret = PXR_MotionTracking.StartBodyTracking(BodyJointSet.BODY_JOINT_SET_BODY_FULL_START, boneLength);

    }

    void Update()
    {
        if (pico == null || smplx == null || pico.LatestRolePoses == null) return;
        if (pico.LatestRolePoses.Length < 24) return;

        Vector3[] W = new Vector3[24];
        Quaternion[] Rw = new Quaternion[24];

        // 把 PICO local（米）转世界
        for (int i = 0; i < 24; i++)
        {
            int src = picoToSmplx[i]; // 允许重映射
            var rp = pico.LatestRolePoses[src];
            var local = rp.localPos * scale;
            var lrot = rp.localRot;

            if (trackingOrigin != null)
            {
                W[i] = trackingOrigin.TransformPoint(local);
                Rw[i] = trackingOrigin.rotation * lrot;
            }
            else
            {
                W[i] = local;
                Rw[i] = lrot;
            }
        }

        Quaternion? headRot = feedHeadAndWristsRotation ? Rw[(int)PositionIndex.head] : (Quaternion?)null;
        Quaternion? lWristRot = feedHeadAndWristsRotation ? Rw[(int)PositionIndex.left_wrist] : (Quaternion?)null;
        Quaternion? rWristRot = feedHeadAndWristsRotation ? Rw[(int)PositionIndex.right_wrist] : (Quaternion?)null;

        // 按 PositionIndex 顺序喂给 ApplySMPL24
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
            W[(int)PositionIndex.right_hand],
            headRot, lWristRot, rWristRot
        );
    }
}
