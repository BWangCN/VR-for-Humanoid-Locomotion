using UnityEngine;
using System;
#if UNITY_EDITOR
using UnityEditor;
#endif
#if UNITY_ANDROID || UNITY_EDITOR
using Unity.XR.PXR;
#endif

public class PXR_SkeletonGenerator : MonoBehaviour
{
    [Header("Visual")]
    public bool addCubes = true;
    public Vector3 cubeSize = new Vector3(0.04f, 0.04f, 0.04f);

    [Header("Cleanup")]
    public bool clearExistingChildren = true;

    [ContextMenu("Generate Pico Skeleton (24)")]
    public void Generate()
    {
#if !(UNITY_ANDROID || UNITY_EDITOR)
        Debug.LogWarning("This generator relies on PICO SDK enum; run in Editor/Android.");
        return;
#endif
        if (clearExistingChildren)
        {
            for (int i = transform.childCount - 1; i >= 0; i--)
            {
#if UNITY_EDITOR
                DestroyImmediate(transform.GetChild(i).gameObject);
#else
                Destroy(transform.GetChild(i).gameObject);
#endif
            }
        }

        // 根据 SDK 的枚举，自动创建同名子节点
        Array roles = Enum.GetValues(typeof(BodyTrackerRole));
        foreach (var r in roles)
        {
            string jointName = r.ToString();
            if (jointName == "ROLE_NUM") continue; // 末尾计数枚举，跳过

            var go = new GameObject(jointName);
            var t = go.transform;
            t.SetParent(this.transform, false);
            t.localPosition = Vector3.zero;
            t.localRotation = Quaternion.identity;
            t.localScale = Vector3.one;

            if (addCubes)
            {
                var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                cube.name = "Cube";
                var ct = cube.transform;
                ct.SetParent(t, false);
                ct.localPosition = Vector3.zero;
                ct.localRotation = Quaternion.identity;
                ct.localScale = cubeSize;

                // 可选：去掉碰撞体，避免物理干扰
                var col = cube.GetComponent<Collider>();
                if (col) DestroyImmediate(col);
            }
        }

        Debug.Log($"[Generator] Created {transform.childCount} joints under '{name}'. Make sure to assign this root to PXR_BodyTrackingBlock.skeletonJoints.");
    }
}

