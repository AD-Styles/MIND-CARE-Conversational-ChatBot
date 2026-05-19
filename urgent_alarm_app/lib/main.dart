import 'package:flutter/material.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';

// 백그라운드에서 알림을 받았을 때 실행되는 핸들러
@pragma('vm:entry-point')
Future<void> _firebaseMessagingBackgroundHandler(RemoteMessage message) async {
  await Firebase.initializeApp();
  print("백그라운드 메시지 수신: ${message.messageId}");
}

void main() async {
  // 1. 필수 초기화
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(); // Firebase 연결

  // 2. 백그라운드 핸들러 등록
  FirebaseMessaging.onBackgroundMessage(_firebaseMessagingBackgroundHandler);

  runApp(const UrgentAlarmApp());
}

class UrgentAlarmApp extends StatelessWidget {
  const UrgentAlarmApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      debugShowCheckedModeBanner: false,
      theme: ThemeData(primarySwatch: Colors.red),
      home: const AlarmScreen(),
    );
  }
}

class AlarmScreen extends StatefulWidget {
  const AlarmScreen({super.key});

  @override
  State<AlarmScreen> createState() => _AlarmScreenState();
}

class _AlarmScreenState extends State<AlarmScreen> {
  String? _token;

  @override
  void initState() {
    super.initState();
    _setupFirebase();
  }

  // Firebase 알림 설정 및 토큰 가져오기
  void _setupFirebase() async {
    FirebaseMessaging messaging = FirebaseMessaging.instance;

    // 알림 권한 요청 (안드로이드 13 이상 필수)
    NotificationSettings settings = await messaging.requestPermission(
      alert: true,
      badge: true,
      sound: true,
    );

    if (settings.authorizationStatus == AuthorizationStatus.authorized) {
      // 3. 내 기기의 고유 토큰 가져오기
      String? token = await messaging.getToken();
      setState(() {
        _token = token;
      });

      print("#############################################");
      print("내 기기 FCM 토큰: $token");
      print("#############################################");
    }

    // 앱이 켜져 있을 때 알림이 오면 실행
    FirebaseMessaging.onMessage.listen((RemoteMessage message) {
      if (message.notification != null) {
        _showAlarmDialog(
          message.notification!.title,
          message.notification!.body,
        );
      }
    });
  }

  // 알림 팝업창 띄우기
  void _showAlarmDialog(String? title, String? body) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: Colors.red.shade900,
        title: Text(
          title ?? "긴급 알림",
          style: const TextStyle(color: Colors.white),
        ),
        content: Text(
          body ?? "알람 신호가 수신되었습니다.",
          style: const TextStyle(color: Colors.white),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text("확인", style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(
              Icons.warning_amber_rounded,
              color: Colors.red,
              size: 100,
            ),
            const SizedBox(height: 20),
            const Text(
              "긴급 상황 발생!",
              style: TextStyle(
                color: Colors.white,
                fontSize: 32,
                fontWeight: FontWeight.bold,
              ),
            ),
            const SizedBox(height: 10),
            const Text(
              "서버로부터 신호를 기다리는 중...",
              style: TextStyle(color: Colors.grey, fontSize: 18),
            ),
            const SizedBox(height: 30),
            const Text(
              "내 기기 토큰 (복사해서 테스트하세요):",
              style: TextStyle(color: Colors.grey, fontSize: 12),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
              child: SelectableText(
                _token ?? "토큰 생성 중...",
                style: const TextStyle(color: Colors.yellow, fontSize: 11),
                textAlign: TextAlign.center,
              ),
            ),
            const SizedBox(height: 20),
            ElevatedButton(
              onPressed: () {
                _showAlarmDialog("테스트 알람", "수신 확인 완료!");
              },
              style: ElevatedButton.styleFrom(backgroundColor: Colors.red),
              child: const Text(
                "알람 수신 테스트",
                style: TextStyle(color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
