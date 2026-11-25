package com.bandwidthcontroller.app;

import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.net.TrafficStats;
import android.os.Build;
import android.webkit.JavascriptInterface;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

public class WebAppInterface {

    private final MainActivity activity;
    private double currentLimitKb = 0;
    private String currentUnit = "MB";
    private String currentScope = "all";
    private ArrayList<String> selectedPackages = new ArrayList<>();
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
    public String getInstalledApps() {
        JSONArray arr = new JSONArray();
        try {
            PackageManager pm = activity.getPackageManager();
            List<ApplicationInfo> apps = pm.getInstalledApplications(PackageManager.GET_META_DATA);
            List<JSONObject> list = new ArrayList<>();
            String selfPkg = activity.getPackageName();

            for (ApplicationInfo app : apps) {
                if (selfPkg.equals(app.packageName)) continue;
                // Include user apps and launchable system apps (e.g. Chrome, YouTube)
                boolean isUserApp = (app.flags & ApplicationInfo.FLAG_SYSTEM) == 0;
                boolean hasLauncher = pm.getLaunchIntentForPackage(app.packageName) != null;
                if (!isUserApp && !hasLauncher) continue;

                String label = pm.getApplicationLabel(app).toString();
                JSONObject obj = new JSONObject();
                obj.put("name", label);
                obj.put("package", app.packageName);
                list.add(obj);
            }

            Collections.sort(list, Comparator.comparing(o -> o.optString("name", "").toLowerCase()));
            for (JSONObject o : list) {
                arr.put(o);
            }
        } catch (Exception ignored) {}
        return arr.toString();
    }

    @JavascriptInterface
    public String applyLimit(double limitKb, String unit, String scope, String packagesJson) {
        this.currentLimitKb = limitKb;
        this.currentUnit = unit != null ? unit : "MB";
        this.currentScope = scope != null ? scope : "all";
        this.selectedPackages = new ArrayList<>();

        try {
            if (packagesJson != null && !packagesJson.isEmpty()) {
                JSONArray pkgs = new JSONArray(packagesJson);
                for (int i = 0; i < pkgs.length(); i++) {
                    selectedPackages.add(pkgs.getString(i));
                }
            }
        } catch (Exception ignored) {}

        if (limitKb <= 0) {
            return disableLimit();
        }

        this.isEnabled = true;
        activity.runOnUiThread(activity::requestVpnPermission);

        JSONObject result = new JSONObject();
        try {
            result.put("success", true);
            result.put("state", getStatusJson());
            String msg = "selected".equals(currentScope)
                ? "Bandwidth limit applied to " + selectedPackages.size() + " selected app(s)."
                : "Bandwidth limit applied to all apps.";
            result.put("message", msg);
        } catch (Exception ignored) {}
        return result.toString();
    }

    public void onVpnPermissionGranted() {
        Intent intent = new Intent(activity, BandwidthVpnService.class);
        intent.putExtra(BandwidthVpnService.EXTRA_LIMIT_KB, currentLimitKb);
        intent.putExtra(BandwidthVpnService.EXTRA_SCOPE, currentScope);
        intent.putStringArrayListExtra(BandwidthVpnService.EXTRA_PACKAGES, selectedPackages);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            activity.startForegroundService(intent);
        } else {
            activity.startService(intent);
        }
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
        } catch (Exception ignored) {}
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
            obj.put("scope", currentScope);
            obj.put("selected_count", selectedPackages.size());
            obj.put("is_admin", true);
        } catch (Exception ignored) {}
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
            stats.put("total_gb_recv", Math.round(((double) rx / (1024.0 * 1024.0 * 1024.0)) * 100.0) / 100.0);
            stats.put("total_gb_sent", Math.round(((double) tx / (1024.0 * 1024.0 * 1024.0)) * 100.0) / 100.0);
        } catch (Exception ignored) {}
        return stats.toString();
    }
}
