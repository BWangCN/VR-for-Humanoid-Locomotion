using UnityEngine;

[RequireComponent(typeof(SkinnedMeshRenderer))]
public class HeadlessClipController : MonoBehaviour
{
    [Tooltip("指向 head(15) 关节的 Transform")]
    public Transform headJoint;

    [Tooltip("裁剪平面法线（世界空间）。通常用世界向上即可；若需跟颈->头方向一致，可在代码里改）")]
    public Vector3 planeNormal = Vector3.up;

    [Tooltip("往下压一点避免颈部边界露出：正值表示把裁剪平面沿法线反向下移")]
    public float pushDown = 0.01f; // 1cm

    [Header("Shader 属性名（需与材质一致）")]
    public string planeOriginProp = "_ClipOrigin";
    public string planeNormalProp = "_ClipNormal";

    SkinnedMeshRenderer smr;
    MaterialPropertyBlock mpb;

    void Awake()
    {
        smr = GetComponentInChildren<SkinnedMeshRenderer>(true);
        mpb = new MaterialPropertyBlock();
    }

    void LateUpdate()
    {
        if (!headJoint || !smr) return;

        // 平面原点 = 头关节位置 - 法线方向 * pushDown
        Vector3 origin = headJoint.position - planeNormal.normalized * pushDown;

        smr.GetPropertyBlock(mpb);
        mpb.SetVector(planeOriginProp, origin);
        mpb.SetVector(planeNormalProp, planeNormal.normalized);
        smr.SetPropertyBlock(mpb);
    }
}

