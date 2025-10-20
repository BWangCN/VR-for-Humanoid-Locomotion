using UnityEngine;
using System;
using System.Collections;



/// <summary>
/// Position index of joint points
/// </summary>
/// 

public enum PositionIndex : int
{
    hip = 0,            // pelvis
    left_hip,           // 1
    right_hip,          // 2
    spine1,             // 3
    left_knee,          // 4
    right_knee,         // 5
    spine2,             // 6
    left_ankle,         // 7
    right_ankle,        // 8
    spine3,             // 9
    left_foot_index,    // 10
    right_foot_index,   // 11
    neck,               // 12
    left_collar,        // 13
    right_collar,       // 14
    head,               // 15
    left_shoulder,      // 16
    right_shoulder,     // 17
    left_elbow,         // 18
    right_elbow,        // 19
    left_wrist,         // 20
    right_wrist,        // 21
    left_hand,          // 22
    right_hand,         // 23
    Count
}

public static partial class EnumExtend
{
    public static int Int(this PositionIndex i)
    {
        return (int)i;
    }
}

public class RealTimeSMPLX : SMPLX
{

    public class JointPoint
    {
        public Vector3 Pos3D = new Vector3();
        public Vector3 Now3D = new Vector3();
        public Vector3[] PrevPos3D = new Vector3[6];

        // Bones
        public Transform Transform = null;
        public Quaternion DefaultPoseRotation;
        public Quaternion Inverse;
        public Quaternion InverseRotation;

        public JointPoint Child = null;
        public JointPoint Parent = null;

        // For Kalman filter
        public Vector3 P = new Vector3();
        public Vector3 X = new Vector3();
        public Vector3 K = new Vector3();
    }

    // Joint position and bone
    private JointPoint[] jointPoints;
    public JointPoint[] JointPoints { get { return jointPoints; } }

    private Vector3 rootPosition; // Initial center position

    private Quaternion InitGazeRotation;
    private Quaternion gazeInverse;

    public GameObject Nose;
    private Animator anim;

    private Camera _mainCamera;

    public new void Awake()
    {
        base.Awake();
        _mainCamera = Camera.main;
    }

    private new void Update()
    {
        base.Update();
        if (jointPoints != null && jointPoints[0].Pos3D != null)
        {
            PoseUpdate();
        }
    }

    public void ApplySMPL24(
    // 0–3 pelvis + spine
    Vector3 pelvis, Vector3 leftHip, Vector3 rightHip, Vector3 spine1,

    // 4–5 knees
    Vector3 leftKnee, Vector3 rightKnee,

    // 6–9 ankles + spine2
    Vector3 spine2, Vector3 leftAnkle, Vector3 rightAnkle, Vector3 spine3,

    // 10–11 feet
    Vector3 leftFoot, Vector3 rightFoot,

    // 12 neck
    Vector3 neck,

    // 13–14 shoulders
    Vector3 leftCollar, Vector3 rightCollar,

    // 15 head
    Vector3 head,

    // 16–17 shoulders
    Vector3 leftShoulder, Vector3 rightShoulder,

    // 18–19 elbows
    Vector3 leftElbow, Vector3 rightElbow,

    // 20–21 wrists
    Vector3 leftWrist, Vector3 rightWrist,

    // 22–23 hands
    Vector3 leftHand, Vector3 rightHand,

    // optional rotations
    Quaternion? headWorldRot = null,
    Quaternion? lWristWorldRot = null,
    Quaternion? rWristWorldRot = null)
    {
        if (jointPoints == null) Init();

        // pelvis & hips & spine
        jointPoints[PositionIndex.hip.Int()].Pos3D = pelvis;
        jointPoints[PositionIndex.left_hip.Int()].Pos3D = leftHip;
        jointPoints[PositionIndex.right_hip.Int()].Pos3D = rightHip;
        jointPoints[PositionIndex.spine1.Int()].Pos3D = spine1;
        jointPoints[PositionIndex.spine2.Int()].Pos3D = spine2;
        jointPoints[PositionIndex.spine3.Int()].Pos3D = spine3;

        // knees
        jointPoints[PositionIndex.left_knee.Int()].Pos3D = leftKnee;
        jointPoints[PositionIndex.right_knee.Int()].Pos3D = rightKnee;

        // ankles
        jointPoints[PositionIndex.left_ankle.Int()].Pos3D = leftAnkle;
        jointPoints[PositionIndex.right_ankle.Int()].Pos3D = rightAnkle;

        // feet
        jointPoints[PositionIndex.left_foot_index.Int()].Pos3D = leftFoot;
        jointPoints[PositionIndex.right_foot_index.Int()].Pos3D = rightFoot;

        // neck + head
        jointPoints[PositionIndex.neck.Int()].Pos3D = neck;
        jointPoints[PositionIndex.head.Int()].Pos3D = head;

        // shoulders
        jointPoints[PositionIndex.left_shoulder.Int()].Pos3D = leftShoulder;
        jointPoints[PositionIndex.right_shoulder.Int()].Pos3D = rightShoulder;

        // elbows
        jointPoints[PositionIndex.left_elbow.Int()].Pos3D = leftElbow;
        jointPoints[PositionIndex.right_elbow.Int()].Pos3D = rightElbow;

        // wrists
        jointPoints[PositionIndex.left_wrist.Int()].Pos3D = leftWrist;
        jointPoints[PositionIndex.right_wrist.Int()].Pos3D = rightWrist;

        // hands
        jointPoints[PositionIndex.left_hand.Int()].Pos3D = leftHand;
        jointPoints[PositionIndex.right_hand.Int()].Pos3D = rightHand;

        // collars
        jointPoints[PositionIndex.left_collar.Int()].Pos3D = leftCollar;
        jointPoints[PositionIndex.right_collar.Int()].Pos3D = rightCollar;

        // root translation
        jointPoints[PositionIndex.hip.Int()].Transform.position = pelvis;

        // optional rotations
        if (headWorldRot.HasValue && jointPoints[PositionIndex.head.Int()].Transform != null)
            jointPoints[PositionIndex.head.Int()].Transform.rotation = headWorldRot.Value;

        if (lWristWorldRot.HasValue && jointPoints[PositionIndex.left_wrist.Int()].Transform != null)
            jointPoints[PositionIndex.left_wrist.Int()].Transform.rotation = lWristWorldRot.Value;

        if (rWristWorldRot.HasValue && jointPoints[PositionIndex.right_wrist.Int()].Transform != null)
            jointPoints[PositionIndex.right_wrist.Int()].Transform.rotation = rWristWorldRot.Value;

        PoseUpdate();
    }


    // Estimate head position using neck and shoulder width
    private Vector3 SynthesizeHead(Vector3 neck, Vector3 lShoulder, Vector3 rShoulder)
    {
        float shoulderWidth = (lShoulder - rShoulder).magnitude;
        Vector3 up = TriangleNormal(neck, rShoulder, lShoulder);
        float headLen = Mathf.Clamp(0.35f * shoulderWidth, 0.05f, 0.25f);
        return neck + up * headLen;
    }

    // Estimate toe position using ankle/knee direction and body forward direction
    private Vector3 SynthesizeToe(Vector3 ankle, Vector3 knee, Vector3 hipCenter, Vector3 lShoulder, Vector3 rShoulder)
    {
        Vector3 shin = (ankle - knee).normalized;
        Vector3 forwardBody = -TriangleNormal((lShoulder + rShoulder) * 0.5f, lShoulder, rShoulder);
        Vector3 dir = (0.7f * forwardBody + 0.3f * (-shin)).normalized;
        float toeLen = Mathf.Clamp((ankle - knee).magnitude * 0.35f, 0.06f, 0.18f);
        return ankle + dir * toeLen;
    }

    // Synthesize hand index/pinky if missing
    private void SynthesizeHandTipsLite(PositionIndex wristIdx, PositionIndex indexIdx, PositionIndex pinkyIdx)
    {
        var w = jointPoints[wristIdx.Int()].Pos3D;
        if (jointPoints[indexIdx.Int()].Pos3D == Vector3.zero)
            jointPoints[indexIdx.Int()].Pos3D = w + new Vector3(0.03f, 0.0f, 0.02f);
        if (jointPoints[pinkyIdx.Int()].Pos3D == Vector3.zero)
            jointPoints[pinkyIdx.Int()].Pos3D = w + new Vector3(-0.03f, 0.0f, 0.02f);
    }

    public JointPoint[] Init()
    {
        jointPoints = new JointPoint[PositionIndex.Count.Int()];
        for (int i = 0; i < PositionIndex.Count.Int(); i++)
            jointPoints[i] = new JointPoint();

        anim = GetComponent<Animator>();

        // ------- Assign Transforms -------
        // Pelvis / Hips
        jointPoints[PositionIndex.hip.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.Hips);
        jointPoints[PositionIndex.left_hip.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftUpperLeg);
        jointPoints[PositionIndex.right_hip.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightUpperLeg);

        // Spine
        jointPoints[PositionIndex.spine1.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.Spine);
        jointPoints[PositionIndex.spine2.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.Chest);
        var upperChest = anim.GetBoneTransform(HumanBodyBones.UpperChest);
        jointPoints[PositionIndex.spine3.Int()].Transform = upperChest != null ? upperChest
                                                                                    : jointPoints[PositionIndex.spine2.Int()].Transform;

        // Neck and Head
        jointPoints[PositionIndex.neck.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.Neck);
        jointPoints[PositionIndex.head.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.Head);

        // Arms - Left
        jointPoints[PositionIndex.left_shoulder.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftUpperArm);
        jointPoints[PositionIndex.left_elbow.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftLowerArm);
        jointPoints[PositionIndex.left_wrist.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftHand);
        jointPoints[PositionIndex.left_hand.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftHand); // same as wrist                                                                                                          

        // Arms - Right
        jointPoints[PositionIndex.right_shoulder.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightUpperArm);
        jointPoints[PositionIndex.right_elbow.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightLowerArm);
        jointPoints[PositionIndex.right_wrist.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightHand);
        jointPoints[PositionIndex.right_hand.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightHand); // same as wrist                                                                                   

        // Legs - Left
        jointPoints[PositionIndex.left_knee.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftLowerLeg);
        jointPoints[PositionIndex.left_ankle.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftFoot);
        jointPoints[PositionIndex.left_foot_index.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.LeftToes);

        // Legs - Right
        jointPoints[PositionIndex.right_knee.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightLowerLeg);
        jointPoints[PositionIndex.right_ankle.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightFoot);
        jointPoints[PositionIndex.right_foot_index.Int()].Transform = anim.GetBoneTransform(HumanBodyBones.RightToes);

        // ------- Assign Hierarchy -------
        // Left Arm
        jointPoints[PositionIndex.left_shoulder.Int()].Child = jointPoints[PositionIndex.left_elbow.Int()];
        jointPoints[PositionIndex.left_elbow.Int()].Parent = jointPoints[PositionIndex.left_shoulder.Int()];
        jointPoints[PositionIndex.left_elbow.Int()].Child = jointPoints[PositionIndex.left_wrist.Int()];
        jointPoints[PositionIndex.left_wrist.Int()].Parent = jointPoints[PositionIndex.left_elbow.Int()];

        // Right Arm
        jointPoints[PositionIndex.right_shoulder.Int()].Child = jointPoints[PositionIndex.right_elbow.Int()];
        jointPoints[PositionIndex.right_elbow.Int()].Parent = jointPoints[PositionIndex.right_shoulder.Int()];
        jointPoints[PositionIndex.right_elbow.Int()].Child = jointPoints[PositionIndex.right_wrist.Int()];
        jointPoints[PositionIndex.right_wrist.Int()].Parent = jointPoints[PositionIndex.right_elbow.Int()];

        // Left Leg
        jointPoints[PositionIndex.left_hip.Int()].Child = jointPoints[PositionIndex.left_knee.Int()];
        jointPoints[PositionIndex.left_knee.Int()].Parent = jointPoints[PositionIndex.left_hip.Int()];
        jointPoints[PositionIndex.left_knee.Int()].Child = jointPoints[PositionIndex.left_ankle.Int()];
        jointPoints[PositionIndex.left_ankle.Int()].Parent = jointPoints[PositionIndex.left_knee.Int()];
        jointPoints[PositionIndex.left_ankle.Int()].Child = jointPoints[PositionIndex.left_foot_index.Int()];
        jointPoints[PositionIndex.left_foot_index.Int()].Parent = jointPoints[PositionIndex.left_ankle.Int()];

        // Right Leg
        jointPoints[PositionIndex.right_hip.Int()].Child = jointPoints[PositionIndex.right_knee.Int()];
        jointPoints[PositionIndex.right_knee.Int()].Parent = jointPoints[PositionIndex.right_hip.Int()];
        jointPoints[PositionIndex.right_knee.Int()].Child = jointPoints[PositionIndex.right_ankle.Int()];
        jointPoints[PositionIndex.right_ankle.Int()].Parent = jointPoints[PositionIndex.right_knee.Int()];
        jointPoints[PositionIndex.right_ankle.Int()].Child = jointPoints[PositionIndex.right_foot_index.Int()];
        jointPoints[PositionIndex.right_foot_index.Int()].Parent = jointPoints[PositionIndex.right_ankle.Int()];

        // ------- Set Inverse Rotations -------
        var pseudoNeckPos = (jointPoints[PositionIndex.left_shoulder.Int()].Transform.position
                            + jointPoints[PositionIndex.right_shoulder.Int()].Transform.position) * 0.5f;

        var forward = TriangleNormal(
            pseudoNeckPos,
            jointPoints[PositionIndex.left_hip.Int()].Transform.position,
            jointPoints[PositionIndex.right_hip.Int()].Transform.position
        );

        foreach (var joint in jointPoints)
        {
            if (joint.Transform != null)
            {
                joint.DefaultPoseRotation = joint.Transform.rotation;

                if (joint.Child != null)
                {
                    joint.Inverse = GetInverse(joint, joint.Child, forward);
                    joint.InverseRotation = joint.Inverse * joint.DefaultPoseRotation;
                }
            }
        }

        // Root setup
        var hip = jointPoints[PositionIndex.hip.Int()];
        rootPosition = _mainCamera.transform.position + new Vector3(0, 0, 5.0f);
        hip.Inverse = Quaternion.Inverse(Quaternion.LookRotation(forward));
        hip.InverseRotation = hip.Inverse * hip.DefaultPoseRotation;

        // Head inverse using head - neck
        var headJoint = jointPoints[PositionIndex.head.Int()];
        headJoint.DefaultPoseRotation = headJoint.Transform.rotation;

        var gazeDir = headJoint.Transform.position - jointPoints[PositionIndex.neck.Int()].Transform.position;
        headJoint.Inverse = Quaternion.Inverse(Quaternion.LookRotation(gazeDir.normalized));
        headJoint.InverseRotation = headJoint.Inverse * headJoint.DefaultPoseRotation;

        return jointPoints;
    }



    public void PoseUpdate()
    {
        var pseudoNeckPosition = (jointPoints[PositionIndex.left_shoulder.Int()].Pos3D +
                                  jointPoints[PositionIndex.right_shoulder.Int()].Pos3D) / 2.0f;

        var forward = TriangleNormal(pseudoNeckPosition,
                                      jointPoints[PositionIndex.left_hip.Int()].Pos3D,
                                      jointPoints[PositionIndex.right_hip.Int()].Pos3D);

        //jointPoints[PositionIndex.hip.Int()].Transform.position = rootPosition;
        jointPoints[PositionIndex.hip.Int()].Transform.position =
    jointPoints[PositionIndex.hip.Int()].Pos3D;   // 用最新 pelvis

        jointPoints[PositionIndex.hip.Int()].Transform.rotation =
            Quaternion.LookRotation(forward) * jointPoints[PositionIndex.hip.Int()].InverseRotation;

        foreach (var jointPoint in jointPoints)
        {
            if (jointPoint.Child != null)
            {
                Vector3 dir = jointPoint.Pos3D - jointPoint.Child.Pos3D;
                jointPoint.Transform.rotation = Quaternion.LookRotation(dir.normalized, forward) * jointPoint.InverseRotation;
            }
        }

        var head = jointPoints[PositionIndex.head.Int()];
        var neck = jointPoints[PositionIndex.neck.Int()];
        var gazeDir = head.Pos3D - neck.Pos3D;

        Vector3 upHint = Vector3.up;
        var spine3 = jointPoints[PositionIndex.spine3.Int()];
        if (spine3 != null)
        {
            upHint = (neck.Pos3D - spine3.Pos3D).normalized;
        }

        //head.Transform.rotation = Quaternion.LookRotation(gazeDir.normalized, upHint) * head.InverseRotation;

        //jointPoints[PositionIndex.left_wrist.Int()].Transform.rotation =
        //jointPoints[PositionIndex.left_wrist.Int()].InverseRotation;

        //jointPoints[PositionIndex.right_wrist.Int()].Transform.rotation =
        //jointPoints[PositionIndex.right_wrist.Int()].InverseRotation;
    }

    Vector3 TriangleNormal(Vector3 a, Vector3 b, Vector3 c)
    {
        Vector3 d1 = a - b;
        Vector3 d2 = a - c;

        Vector3 dd = Vector3.Cross(d1, d2);
        dd.Normalize();

        return dd;
    }

    private Quaternion GetInverse(JointPoint p1, JointPoint p2, Vector3 forward)
    {
        return Quaternion.Inverse(Quaternion.LookRotation(p1.Transform.position - p2.Transform.position, forward));
    }
}