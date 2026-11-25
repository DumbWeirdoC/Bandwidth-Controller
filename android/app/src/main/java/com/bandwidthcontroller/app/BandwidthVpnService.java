package com.bandwidthcontroller.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.ProxyInfo;
import android.net.VpnService;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import java.io.IOException;
import java.util.ArrayList;

public class BandwidthVpnService extends VpnService {

    public static final String ACTION_STOP = "com.bandwidthcontroller.app.STOP";
    public static final String EXTRA_LIMIT_KB = "LIMIT_KB";
    public static final String EXTRA_SCOPE = "SCOPE";
    public static final String EXTRA_PACKAGES = "PACKAGES";

    private static final String CHANNEL_ID = "bandwidth_controller_vpn";
    private static final int NOTIFICATION_ID = 101;

    private ParcelFileDescriptor vpnInterface = null;
    private LocalRateProxy rateProxy = null;

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopVpn();
            return START_NOT_STICKY;
        }

        double limitKb = intent != null ? intent.getDoubleExtra(EXTRA_LIMIT_KB, 0) : 0;
        String scope = intent != null ? intent.getStringExtra(EXTRA_SCOPE) : "all";
        ArrayList<String> packages = intent != null ? intent.getStringArrayListExtra(EXTRA_PACKAGES) : null;

        startForegroundNotification(limitKb, scope, packages != null ? packages.size() : 0);

        if (rateProxy == null) {
            rateProxy = new LocalRateProxy(this);
            rateProxy.start();
        }
        rateProxy.setLimitKb(limitKb);

        establishVpn(scope, packages);
        return START_STICKY;
    }

    private void establishVpn(String scope, ArrayList<String> packages) {
        closeInterface();
        try {
            Builder builder = new Builder();
            builder.setSession("BandwidthController");
            builder.addAddress("10.8.0.2", 32);
            builder.setMtu(1500);

            if ("selected".equals(scope) && packages != null && !packages.isEmpty()) {
                for (String pkg : packages) {
                    try {
                        builder.addAllowedApplication(pkg);
                    } catch (PackageManager.NameNotFoundException ignored) {}
                }
            } else {
                try {
                    builder.addDisallowedApplication(getPackageName());
                } catch (PackageManager.NameNotFoundException ignored) {}
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                builder.setHttpProxy(ProxyInfo.buildDirectProxy("127.0.0.1", LocalRateProxy.PROXY_PORT));
            }

            vpnInterface = builder.establish();
        } catch (Exception ignored) {}
    }

    private void startForegroundNotification(double limitKb, String scope, int count) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID,
                "Bandwidth Controller",
                NotificationManager.IMPORTANCE_LOW
            );
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }

        String scopeLabel = "selected".equals(scope) ? (count + " selected apps") : "All apps";
        String text = String.format("Limit: %.0f KB/s (%s)", limitKb, scopeLabel);

        Notification.Builder nb = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? new Notification.Builder(this, CHANNEL_ID)
            : new Notification.Builder(this);

        Notification notification = nb
            .setContentTitle("Bandwidth Controller Active")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_dialog_dialer)
            .setOngoing(true)
            .build();

        startForeground(NOTIFICATION_ID, notification);
    }

    private void closeInterface() {
        if (vpnInterface != null) {
            try {
                vpnInterface.close();
            } catch (IOException ignored) {}
            vpnInterface = null;
        }
    }

    private void stopVpn() {
        if (rateProxy != null) {
            rateProxy.stop();
            rateProxy = null;
        }
        closeInterface();
        stopForeground(true);
        stopSelf();
    }

    @Override
    public void onDestroy() {
        stopVpn();
        super.onDestroy();
    }
}
