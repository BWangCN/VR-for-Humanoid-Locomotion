using System;
using Unity.XR.PXR;
using UnityEngine;

public class PXR_BodyTrackingBlock : MonoBehaviour
{
    public struct RolePose
    {
        public Vector3 localPos;
        public Quaternion localRot;
        public bool valid;
    }

    public RolePose[] LatestRolePoses { get; private set; } =
        new RolePose[(int)BodyTrackerRole.ROLE_NUM];

    public event Action<RolePose[]> OnPosesUpdated;

    private bool supportedBT = false;
    private bool updateBT = true;

    private BodyTrackingGetDataInfo bdi = new BodyTrackingGetDataInfo();
    private BodyTrackingData bd = new BodyTrackingData();
    private BodyTrackingStatus bs = new BodyTrackingStatus();
    private bool isTracking = false;

    void Start()
    {
#if UNITY_ANDROID
        PXR_MotionTracking.GetBodyTrackingSupported(ref supportedBT);
        if (!supportedBT) { updateBT = false; return; }

        BodyTrackingBoneLength bones = new BodyTrackingBoneLength();
        PXR_MotionTracking.StartBodyTracking(BodyJointSet.BODY_JOINT_SET_BODY_FULL_START, bones);
        updateBT = true;
#endif
    }

    void Update()
    {
#if UNITY_ANDROID
        if (!updateBT) return;

        PXR_MotionTracking.GetBodyTrackingState(ref isTracking, ref bs);
        if (bs.stateCode != BodyTrackingStatusCode.BT_VALID) return;

        if (PXR_MotionTracking.GetBodyTrackingData(ref bdi, ref bd) != 0) return;

        for (int i = 0; i < (int)BodyTrackerRole.ROLE_NUM; i++)
        {
            LatestRolePoses[i] = new RolePose
            {
                localPos = new Vector3(
                    (float)bd.roleDatas[i].localPose.PosX,
                    (float)bd.roleDatas[i].localPose.PosY,
                    (float)bd.roleDatas[i].localPose.PosZ),
                localRot = new Quaternion(
                    (float)bd.roleDatas[i].localPose.RotQx,
                    (float)bd.roleDatas[i].localPose.RotQy,
                    (float)bd.roleDatas[i].localPose.RotQz,
                    (float)bd.roleDatas[i].localPose.RotQw),
                valid = true
            };
        }
        OnPosesUpdated?.Invoke(LatestRolePoses);
#endif
    }

    void OnDestroy()
    {
#if UNITY_ANDROID
        PXR_MotionTracking.StopBodyTracking();
        updateBT = false;
#endif
    }
}
