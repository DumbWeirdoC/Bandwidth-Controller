package com.bandwidthcontroller.app;

import android.content.Context;
import android.content.Intent;
import android.net.TrafficStats;
import android.webkit.JavascriptInterface;
import org.json.JSONArray;
import org.json.JSONObject;

public class WebAppInterface {

    private final MainActivity activity;
    private double currentLimitKb = 0;
    private String currentUnit = "MB";
    private boolean isEnabled = false;

    private long lastTime = System.currentTimeMillis();
    private long lastRxBytes = TrafficStats.getTotalRxBytes();
    private long lastTxBytes = TrafficStats.getTotalTxBytes();

    private double downloadKb = 0;
    private double uploadKb = 0;

    public WebAppInterface(MainActivity activity) {
        this.activity = activity;
    }

    @JavascriptInterface
    public String applyLimit(double limitKb, String unit) {
        this.currentLimitKb = limitKb;
        this.currentUnit = unit;
        
        activity.runOnUiThread(activity::requestVpnPermission);

        JSONObject result = new JSONObject();
        try {
            result.put("success", true);
            result.put("state", getStatusJson());
            result.put("message", "Bandwidth limit applied via Android VpnService.");
        } catch (Exception e) {
            e.printStackTrace();
        }
        return result.toString();
    }

    public void onVpnPermissionGranted() {
        isEnabled = true;
        Intent intent = new Intent(activity, BandwidthVpnService.class);
        intent.putExtra("LIMIT_KB", currentLimitKb);
        activity.startService(intent);
    }

    @JavascriptInterface
    public String disableLimit() {
        isEnabled = false;
        Intent intent = new Intent(activity, BandwidthVpnService.class);
        intent.setAction(BandwidthVpnService.ACTION_STOP);
        activity.startService(intent);

        JSONObject result = new JSONObject();
        try {
            result.put("success", true);
            result.put("state", getStatusJson());
            result.put("message", "Bandwidth throttling disabled.");
        } catch (Exception e) {
            e.printStackTrace();
        }
        return result.toString();
    }

    @JavascriptInterface
    public String getStatus() {
        return getStatusJson().toString();
    }

    private JSONObject getStatusJson() {
        JSONObject obj = new JSONObject();
        try {
            obj.put("enabled", isEnabled);
            obj.put("limit_kb", currentLimitKb);
            obj.put("limit_mb", Math.round((currentLimitKb / 1024.0) * 1000.0) / 1000.0);
            obj.put("unit", currentUnit);
            obj.put("is_admin", true);
        } catch (Exception e) {
            e.printStackTrace();
        }
        return obj;
    }

    @JavascriptInterface
    public String getStats() {
        long now = System.currentTimeMillis();
        long elapsed = now - lastTime;
        long rx = TrafficStats.getTotalRxBytes();
        long tx = TrafficStats.getTotalTxBytes();

        if (elapsed > 100) {
            long deltaRx = Math.max(0, rx - lastRxBytes);
            long deltaTx = Math.max(0, tx - lastTxBytes);

            downloadKb = Math.round(((double) deltaRx / (elapsed / 1000.0) / 1024.0) * 10.0) / 10.0;
            uploadKb = Math.round(((double) deltaTx / (elapsed / 1000.0) / 1024.0) * 10.0) / 10.0;

            lastTime = now;
            lastRxBytes = rx;
            lastTxBytes = tx;
        }

        JSONObject stats = new JSONObject();
        try {
            stats.put("download_kb", downloadKb);
            stats.put("upload_kb", uploadKb);
            stats.put("download_mb", Math.round((downloadKb / 1024.0) * 1000.0) / 1000.0);
            stats.put("upload_mb", Math.round((uploadKb / 1024.0) * 1000.0) / 1000.0);
            stats.put("total_bytes_recv", rx);
            stats.put("total_bytes_sent", tx);
            stats.put("total_gb_recv", Math.round(((double) rx / (1024.0 * 1024.0 * 1024.0)) * 100.0) / 100.0);
            stats.put("total_gb_sent", Math.round(((double) tx / (1024.0 * 1024.0 * 1024.0)) * 100.0) / 100.0);

            JSONArray ifaces = new JSONArray();
            JSONObject iface = new JSONObject();
            iface.put("name", "wlan0 / rmnet");
            ifaces.put(iface);
            stats.put("interfaces", ifaces);
        } catch (Exception e) {
            e.printStackTrace();
        }
        return stats.toString();
    }
}
