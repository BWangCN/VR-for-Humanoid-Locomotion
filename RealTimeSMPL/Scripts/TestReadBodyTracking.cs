using UnityEngine;

public class TestReadBodyTracking : MonoBehaviour
{
    public PXR_BodyTrackingBlock bodyTracker;   // 或 PXR_BodyTrackingDataFeed（你用哪个就写哪个类名）

    void Update()
    {
        if (bodyTracker == null || bodyTracker.LatestRolePoses == null)
            return;

        var poses = bodyTracker.LatestRolePoses;

        // 打印全部关节点
        for (int i = 0; i < poses.Length; i++)
        {
            var pose = poses[i];

            // 只打印有效的（valid = true）
            if (pose.valid)
            {
                Debug.Log($"Index {i}: Pos = {pose.localPos}, Rot = {pose.localRot}");
            }
            else
            {
                Debug.Log($"Index {i}: INVALID");
            }
        }
    }
}
